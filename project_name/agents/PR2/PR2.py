import sys
import jax
import jax.numpy as jnp
from typing import Any
import jax.random as jrandom
from functools import partial
from project_name.agents.PR2.network import ActorPR2, JointCriticPR2, IndCriticPR2, \
    OppNetworkPR2  # TODO sort out this class import ting
import optax
from flax.training.train_state import TrainState
from project_name.utils import MemoryState
import flashbax as fbx
from typing import NamedTuple
import flax
from project_name.agents import AgentBase
from project_name.utils import remove_element
from project_name.agents.PR2.kernel import adaptive_isotropic_gaussian_kernel


class TrainStateExt(TrainState):
    target_params: flax.core.FrozenDict


class TrainStatePR2(NamedTuple):  # TODO is this correct tag?
    joint_critic_state: TrainStateExt
    ind_critic_state: TrainState
    actor_state: TrainStateExt
    opp_state: TrainState


class TransitionPR2(NamedTuple):
    done: jnp.ndarray
    action: jnp.ndarray
    reward: jnp.ndarray
    obs: jnp.ndarray


class PR2Agent(AgentBase):
    def __init__(self,
                 env,
                 env_params,
                 key,
                 config):
        self.config = config
        self.env = env
        self.env_params = env_params
        self.joint_critic_network = JointCriticPR2(config=config)
        self.ind_critic_network = IndCriticPR2(config=config)
        self.actor_network = ActorPR2(action_dim=env.action_space(env_params).n,
                                      config=config)
        self.opp_network = OppNetworkPR2(action_dim=config.NUM_AGENTS - 1, config=config)

        key, _key = jrandom.split(key)

        init_x = (jnp.zeros((1, config.NUM_ENVS, env.observation_space(env_params).n)),
                  jnp.zeros((1, config.NUM_ENVS)),
                  )

        self.joint_critic_network_params = self.joint_critic_network.init(_key, init_x,
                                                                          jnp.zeros((1, config.NUM_ENVS, 1)),
                                                                          jnp.zeros((1, config.NUM_ENVS,
                                                                                     config.NUM_AGENTS - 1)))
        self.ind_critic_network_params = self.ind_critic_network.init(_key, init_x,
                                                                      jnp.zeros((1, config.NUM_ENVS, 1)))
        self.actor_network_params = self.actor_network.init(_key, init_x)
        self.opp_network_params = self.opp_network.init(_key, jnp.zeros(
            (1, config.NUM_ENVS, 1, env.observation_space(env_params).n)),
                                                        jnp.zeros((1, config.NUM_ENVS, 1, 1)),
                                                        jnp.zeros((1, config.NUM_ENVS, 1,
                                                                   config.NUM_AGENTS - 1))
                                                        )

        self.per_buffer = fbx.make_prioritised_flat_buffer(max_length=config.BUFFER_SIZE,
                                                           min_length=config.BATCH_SIZE,
                                                           sample_batch_size=config.BATCH_SIZE,
                                                           add_sequences=True,
                                                           add_batch_size=None,
                                                           priority_exponent=config.REPLAY_PRIORITY_EXP,
                                                           device=config.DEVICE)

        self.per_buffer = self.per_buffer.replace(init=jax.jit(self.per_buffer.init),
                                                  add=jax.jit(self.per_buffer.add, donate_argnums=0),
                                                  sample=jax.jit(self.per_buffer.sample),
                                                  can_sample=jax.jit(self.per_buffer.can_sample),
                                                  )

        def linear_schedule(count):  # TODO put this somewhere better
            frac = (1.0 - (count // (config["NUM_MINIBATCHES"] * config["UPDATE_EPOCHS"])) / config["NUM_UPDATES"])
            return config["LR"] * frac

        if config["ANNEAL_LR"]:
            self.tx = optax.chain(optax.clip_by_global_norm(config["MAX_GRAD_NORM"]),
                                  optax.adam(learning_rate=linear_schedule, eps=1e-5),
                                  )
        else:
            self.tx = optax.chain(optax.clip_by_global_norm(config["MAX_GRAD_NORM"]),
                                  optax.adam(config["LR"], eps=1e-5),
                                  )

    def create_train_state(self):
        return (TrainStatePR2(joint_critic_state=TrainStateExt.create(apply_fn=self.joint_critic_network.apply,
                                                                      params=self.joint_critic_network_params,
                                                                      target_params=self.joint_critic_network_params,
                                                                      tx=self.tx),
                              ind_critic_state=TrainState.create(apply_fn=self.ind_critic_network.apply,
                                                                 params=self.ind_critic_network_params,
                                                                 tx=self.tx),
                              actor_state=TrainStateExt.create(apply_fn=self.actor_network.apply,
                                                               params=self.actor_network_params,
                                                               target_params=self.actor_network_params,
                                                               tx=self.tx),
                              opp_state=TrainState.create(apply_fn=self.opp_network.apply,
                                                          params=self.opp_network_params,
                                                          tx=self.tx)),
                self.per_buffer.init(
                    TransitionPR2(done=jnp.zeros((self.config.NUM_ENVS), dtype=bool),
                                  action=jnp.zeros((self.config.NUM_AGENTS, self.config.NUM_ENVS), dtype=jnp.int32),
                                  reward=jnp.zeros((self.config.NUM_ENVS)),
                                  obs=jnp.zeros((self.config.NUM_ENVS, self.env.observation_space(self.env_params).n),
                                                dtype=jnp.int8),
                                  # TODO is it always an int for the obs?
                                  )))

    @partial(jax.jit, static_argnums=(0,))
    def reset_memory(self,
                     mem_state):  # TODO don't think should ever reset the buffer right? but should reset the rest?
        return mem_state

    @partial(jax.jit, static_argnums=(0,))
    def act(self, train_state: Any, mem_state: Any, ac_in: Any, key: Any):  # TODO better implement checks
        pi, action_logits = train_state.actor_state.apply_fn(train_state.actor_state.params,
                                                             ac_in)  # TODO should this be target params or actual params?
        # value = train_state.critic_state.apply_fn(train_state.critic_state.params, ac_in)  # TODO same as above
        value = jnp.zeros((1))  # TODO don't need to track it for PR2 update but double check
        key, _key = jrandom.split(key)
        action = pi.sample(seed=_key)
        log_prob = pi.log_prob(action)

        return mem_state, action, log_prob, value, key

    @partial(jax.jit, static_argnums=(0,))
    def update(self, runner_state, agent, traj_batch):
        train_state, mem_state, env_state, ac_in, key = runner_state

        mem_state = self.per_buffer.add(mem_state, TransitionPR2(done=traj_batch.done[:, agent, :],
                                                                 action=traj_batch.action,
                                                                 reward=traj_batch.reward[:, agent, :],
                                                                 obs=traj_batch.obs[:, agent, :],
                                                                 ))

        key, _key = jrandom.split(key)
        batch = self.per_buffer.sample(mem_state, _key)

        # CRITIC training
        def _joint_critic_loss(critic_target_params, critic_params, opp_params, batch, key):
            obs = batch.experience.first.obs
            action = batch.experience.first.action
            reward = batch.experience.first.reward
            done = batch.experience.first.done
            nobs = batch.experience.second.obs
            naction = batch.experience.second.action
            ndone = batch.experience.second.done

            naction = jnp.swapaxes(naction, 1, 2)
            naction_ego = jnp.expand_dims(naction[:, :, agent], -1)
            # naction_opp = train_state.opp_state.apply_fn(opp_params, obs, naction_ego)
            naction_opp = jrandom.uniform(key, (self.config.BATCH_SIZE, self.config.NUM_ENVS, self.config.VALUE_N_PARTICLES, 1))
            # TODO could this not be the actions that were actually taken by opponents ??

            # shape batch_size, num_envs, value_n_particles
            nobs = jnp.tile(jnp.expand_dims(nobs, axis=-2), (1, 1, self.config.VALUE_N_PARTICLES, 1))
            ndone = jnp.tile(jnp.expand_dims(ndone, axis=-1), (1, 1, self.config.VALUE_N_PARTICLES))
            naction_ego = jnp.tile(jnp.expand_dims(naction_ego, axis=-2), (1, 1, self.config.VALUE_N_PARTICLES, 1))
            joint_target_value = train_state.joint_critic_state.apply_fn(critic_target_params,
                                                                         (nobs, ndone),
                                                                         naction_ego,
                                                                         naction_opp)

            # use Q-values only for the taken actions
            action = jnp.swapaxes(action, 1, 2)
            action_ego = jnp.expand_dims(action[:, :, agent], -1)
            action_opp = remove_element(action, agent)
            joint_q = train_state.joint_critic_state.apply_fn(critic_params, (obs, jnp.expand_dims(done, axis=-1)),
                                                              action_ego,
                                                              action_opp)

            target_value = self.config.ANNEALING * jnp.log(
                jnp.sum(jnp.exp((joint_target_value / self.config.ANNEALING)), axis=-1))
            target_value -= jnp.log(self.config.VALUE_N_PARTICLES)
            target_value += (1) * jnp.log(2)  # TODO should be opponent action dim?

            target_q = jax.lax.stop_gradient(reward + (1 - done) * self.config.GAMMA * (target_value))

            critic_loss = 0.5 * jnp.mean(jnp.square(target_q - joint_q))

            return critic_loss, target_q

        key, _key = jrandom.split(key)
        (joint_critic_loss, target_q), grads = jax.value_and_grad(_joint_critic_loss, argnums=1, has_aux=True)(
            train_state.joint_critic_state.target_params,
            train_state.joint_critic_state.params,
            train_state.opp_state.params,
            batch,
            _key
        )

        train_state = train_state._replace(
            joint_critic_state=train_state.joint_critic_state.apply_gradients(grads=grads))  # TODO check this works

        def _ind_critic_loss(critic_params, batch, joint_q):
            obs = batch.experience.first.obs
            action = batch.experience.first.action
            done = batch.experience.first.done

            action = jnp.swapaxes(action, 1, 2)
            action_ego = jnp.expand_dims(action[:, :, agent], -1)

            ind_q = train_state.ind_critic_state.apply_fn(critic_params, (obs, jnp.expand_dims(done, axis=-1)),
                                                            action_ego)

            critic_loss = 0.5 * jnp.mean(jnp.square(joint_q - ind_q))

            return critic_loss

        key, _key = jrandom.split(key)
        ind_critic_loss, grads = jax.value_and_grad(_ind_critic_loss, argnums=0)(
            train_state.ind_critic_state.params,
            batch,
            target_q
        )

        train_state = train_state._replace(
            ind_critic_state=train_state.ind_critic_state.apply_gradients(grads=grads))

        def _actor_loss(actor_params, critic_params, opp_params, batch, key):
            obs = batch.experience.first.obs
            action = batch.experience.first.action
            reward = batch.experience.first.reward
            done = batch.experience.first.done
            nobs = batch.experience.second.obs
            naction = batch.experience.second.action
            ndone = batch.experience.second.done

            # actor part
            pi, action_logits = train_state.actor_state.apply_fn(actor_params, (
                obs, jnp.expand_dims(done, axis=-1)))  # TODO remove done part at some point as not needed
            action_ego = jnp.expand_dims(pi.sample(seed=key), -1)  # TODO actions bit dodge as tryna do discrete with continuous the rest lol

            obs = jnp.tile(jnp.expand_dims(obs, axis=-2), (1, 1, self.config.VALUE_N_PARTICLES, 1))
            action_ego = jnp.tile(jnp.expand_dims(action_ego, axis=-2), (1, 1, self.config.VALUE_N_PARTICLES, 1))
            latents = jrandom.normal(key, action_ego.shape)
            action_opp = train_state.opp_state.apply_fn(opp_params, obs, action_ego, latents)

            nobs = jnp.tile(jnp.expand_dims(nobs, axis=-2), (1, 1, self.config.VALUE_N_PARTICLES, 1))
            ndone = jnp.tile(jnp.expand_dims(ndone, axis=-1), (1, 1, self.config.VALUE_N_PARTICLES))
            q_targets = train_state.joint_critic_state.apply_fn(critic_params, (nobs, ndone),
                                                         action_ego,
                                                         action_opp)

            q_targets = self.config.ANNEALING * jnp.log(
                jnp.sum(jnp.exp((q_targets / self.config.ANNEALING)), axis=-1))
            q_targets -= jnp.log(self.config.VALUE_N_PARTICLES)
            q_targets += (1) * jnp.log(2)  # TODO should be opponent action dim not 1?

            pg_loss = -jnp.mean(q_targets)

            # TODO aux loss for greater levels of k

            return pg_loss

        key, _key = jrandom.split(key)
        actor_loss, grads = jax.value_and_grad(_actor_loss, argnums=0)(train_state.actor_state.params,
                                                                       train_state.joint_critic_state.params,
                                                                       train_state.opp_state.params,
                                                                       batch,
                                                                       _key
                                                                       )
        train_state = train_state._replace(actor_state=train_state.actor_state.apply_gradients(grads=grads))

        def _opp_policy_loss(ind_critic_params, joint_critic_params, opp_params, batch, key):
            obs = batch.experience.first.obs
            action = batch.experience.first.action
            reward = batch.experience.first.reward
            done = batch.experience.first.done
            nobs = batch.experience.second.obs
            naction = batch.experience.second.action
            ndone = batch.experience.second.done

            action = jnp.swapaxes(action, 1, 2)
            action_ego = jnp.expand_dims(action[:, :, agent], -1)
            new_obs = jnp.tile(jnp.expand_dims(obs, axis=-2), (1, 1, self.config.KERNEL_N_PARTICLES, 1))
            new_action_ego = jnp.tile(jnp.expand_dims(action_ego, axis=-2), (1, 1, self.config.KERNEL_N_PARTICLES, 1))
            latents = jrandom.normal(key, new_action_ego.shape)
            action_opp = train_state.opp_state.apply_fn(opp_params, new_obs, new_action_ego, latents)

            n_updated_actions = int(self.config.KERNEL_N_PARTICLES * self.config.KERNEL_UPDATE_RATIO)
            n_fixed_actions = self.config.KERNEL_N_PARTICLES - n_updated_actions

            combo_actions = jnp.split(action_opp, [n_fixed_actions, n_updated_actions], axis=-2)
            fixed_actions = combo_actions[0]
            updated_actions = combo_actions[2]
            fixed_actions = jax.lax.stop_gradient(fixed_actions)

            def _grad_log_p(fixed_actions):
                new_obs = jnp.tile(jnp.expand_dims(obs, axis=-2), (1, 1, n_fixed_actions, 1))
                new_action_ego = jnp.tile(jnp.expand_dims(action_ego, axis=-2), (1, 1, n_fixed_actions, 1))
                new_done = jnp.tile(jnp.expand_dims(done, axis=-1), (1, 1, n_fixed_actions))
                svgd_q_target = train_state.joint_critic_state.apply_fn(joint_critic_params, (new_obs, new_done),
                                                                  new_action_ego,
                                                                  fixed_actions)

                baseline_ind_q = train_state.ind_critic_state.apply_fn(ind_critic_params, (obs, jnp.expand_dims(done, axis=-1)),
                                                                action_ego)
                baseline_ind_q = jnp.tile(jnp.expand_dims(baseline_ind_q, axis=-1), (1, 1, n_fixed_actions))
                svgd_q_target = (svgd_q_target - baseline_ind_q) / self.config.ANNEALING

                squash_correction = jnp.sum(jnp.log(1 - fixed_actions ** 2 + 1e-6), axis=-1)

                log_p = svgd_q_target + squash_correction

                return log_p

            def vgrad(f, x):
                y, vjp_fn = jax.vjp(f, x)
                return vjp_fn(jnp.ones(y.shape))[0]

            grad_log_p = vgrad(_grad_log_p, fixed_actions)

            # dims are batch_size, num_envs, n_fixed_actions, 1, opponent_action_dim?
            grad_log_p = jnp.expand_dims(grad_log_p, axis=-2)
            grad_log_p = jax.lax.stop_gradient(grad_log_p)

            kernel_dict = adaptive_isotropic_gaussian_kernel(xs=fixed_actions, ys=updated_actions)

            kappa = jnp.expand_dims(kernel_dict["output"], axis=-1)

            action_gradients = jnp.mean(kappa * grad_log_p + kernel_dict["gradient"], axis=2)

            def _policy_grads(backprop_policy_grad_params, updated_actions, action_gradients):
                action_opp = train_state.opp_state.apply_fn(backprop_policy_grad_params, new_obs, updated_actions, latents)

                return jnp.mean(action_opp * action_gradients)  # TODO fuck knows if this works

            # new_obs = jnp.tile(jnp.expand_dims(obs, axis=-2), (1, 1, n_updated_actions, 1))
            # new_action_ego = jnp.tile(jnp.expand_dims(action_ego, axis=-2), (1, 1, n_updated_actions, 1))
            # latents = jrandom.normal(key, new_action_ego.shape)
            # gradients = jax.grad(_policy_grads, argnums=0)(opp_params, updated_actions, action_gradients)

            gradients = vgrad(updated_actions, opp_params)

            surrogate_loss = [w * g for w, g in zip(opp_params, gradients)]

            return -surrogate_loss

        key, _key = jrandom.split(key)
        opp_loss, grads = jax.value_and_grad(_opp_policy_loss, argnums=2)(train_state.ind_critic_state.params,
                                                                          train_state.joint_critic_state.params,
                                                                          train_state.opp_state.params,
                                                                          batch,
                                                                          _key)
        train_state = train_state._replace(opp_state=train_state.opp_state.apply_gradients(grads=grads))

        return train_state, mem_state, env_state, ac_in, key
