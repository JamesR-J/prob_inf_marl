# Adapted from https://github.com/ucl-dark/pax/blob/main/pax/agents/mfos_ppo/ppo_gru.py

import jax
import jax.numpy as jnp
import jax.random as jrandom
import optax
from typing import Any, Dict, Mapping, NamedTuple, Tuple
from project_name.agents.MFOS.network import ActorCriticMFOSRNN, ScannedMFOSRNN
from flax.training.train_state import TrainState
from functools import partial
import sys


class MemoryStateMFOS(NamedTuple):
    """State consists of network extras (to be batched)"""

    hstate: jnp.ndarray
    th: jnp.ndarray
    curr_th: jnp.ndarray
    extras: Mapping[str, jnp.ndarray]


class MFOSAgent:
    """A simple PPO agent with memory using JAX"""

    def __init__(self,
                 env,
                 env_params,
                 key,
                 config):
        self.config = config
        self.env = env
        self.env_params = env_params
        self.network = ActorCriticMFOSRNN(env.action_space().n, config=config)

        key, _key = jrandom.split(key)
        init_hstate = ScannedMFOSRNN.initialize_carry(config["NUM_ENVS"], config.GRU_HIDDEN_DIM)  # TODO remove this
        init_th = jnp.zeros((1, config.NUM_ENVS, config.GRU_HIDDEN_DIM // 3))

        if self.config.CNN:
            init_x = ((jnp.zeros((1, config.NUM_ENVS, *env.observation_space(env_params)["observation"].shape)),
                       jnp.zeros((1, config.NUM_ENVS, env.observation_space(env_params)["inventory"].shape))),
                      jnp.zeros((1, config.NUM_ENVS)),
                      )
        else:
            init_x = (jnp.zeros((1, config.NUM_ENVS, env.observation_space(env_params).n)),
                      jnp.zeros((1, config.NUM_ENVS)),
                      )
            self.network_params = self.network.init(_key, init_hstate, init_x, init_th)

        self.network_params = self.network.init(_key, init_hstate, init_x, init_th)
        self.init_hstate = ScannedMFOSRNN.initialize_carry(config["NUM_ENVS"],
                                                           config["GRU_HIDDEN_DIM"])  # TODO do we need both?

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
        return (TrainState.create(apply_fn=self.network.apply,
                                  params=self.network_params,
                                  tx=self.tx),
                MemoryStateMFOS(hstate=self.init_hstate,
                            th=jnp.ones((self.config.NUM_ENVS, self.config.GRU_HIDDEN_DIM // 3)),
                            curr_th=jnp.ones((self.config.NUM_ENVS, self.config.GRU_HIDDEN_DIM // 3)),
                            extras={
                                "values": jnp.zeros((self.config.NUM_ENVS, 1)),
                                "log_probs": jnp.zeros((self.config.NUM_ENVS, 1)),
                            }, ),
                )

    @partial(jax.jit, static_argnums=(0,))
    def reset_memory(self, mem_state):
        mem_state = mem_state._replace(extras={
            "values": jnp.zeros((self.config.NUM_ENVS, 1)),
            "log_probs": jnp.zeros((self.config.NUM_ENVS, 1)),
        },
            hstate=jnp.zeros((self.config.NUM_ENVS, self.config.GRU_HIDDEN_DIM)),
            th=jnp.ones((self.config.NUM_ENVS, self.config.GRU_HIDDEN_DIM // 3)),
            curr_th=jnp.ones((self.config.NUM_ENVS, self.config.GRU_HIDDEN_DIM // 3)),
        )
        return mem_state

    @partial(jax.jit, static_argnums=(0,))
    def meta_policy(self, mem_state):
        mem_state = mem_state._replace(th=mem_state.curr_th)

        # reset memory of agent
        mem_state = mem_state._replace(hstate=jnp.zeros_like(mem_state.hstate),
                                       curr_th=jnp.ones_like(mem_state.curr_th),
                                       )

        return mem_state

    @partial(jax.jit, static_argnums=(0,))
    def act(self, train_state: Any, mem_state: Any, ac_in: Any, key: Any):  # TODO better implement checks
        hstate, pi, value, action_logits, curr_th = train_state.apply_fn(train_state.params, mem_state.hstate, ac_in,
                                                                         mem_state.th)
        key, _key = jrandom.split(key)
        action = pi.sample(seed=_key)
        log_prob = pi.log_prob(action)

        mem_state.extras["values"] = jnp.swapaxes(value, 0, 1)
        mem_state.extras["log_probs"] = jnp.swapaxes(log_prob, 0, 1)  # TODO sort this out a bit

        mem_state = mem_state._replace(hstate=hstate, curr_th=jnp.squeeze(curr_th, axis=0), extras=mem_state.extras)

        return mem_state, action, log_prob, value, key

    @partial(jax.jit, static_argnums=(0,))
    def update(self, runner_state, agent, traj_batch):
        train_state, mem_state, env_state, ac_in, key = runner_state
        return train_state, mem_state, env_state, ac_in, key

    @partial(jax.jit, static_argnums=(0,2))
    def meta_update(self, runner_state, agent, traj_batch):
        # new_mem_state = jax.tree_map(lambda x: x[:, jnp.newaxis, :], traj_batch.mem_state[agent])
        # traj_batch = traj_batch._replace(mem_state=new_mem_state)
        traj_batch = jax.tree_map(lambda x: x[:, agent], traj_batch)
        # CALCULATE ADVANTAGE
        train_state, mem_state, env_state, ac_in, key = runner_state
        # ac_in = (last_obs[jnp.newaxis, :],
        #          last_done[jnp.newaxis, :],
        #          )
        _, _, last_val, _, _ = train_state.apply_fn(train_state.params, mem_state.hstate, ac_in, mem_state.th)
        last_val = last_val.squeeze()

        def _calculate_gae(traj_batch, last_val):
            def _get_advantages(gae_and_next_value, transition):
                gae, next_value = gae_and_next_value
                done, value, reward = (
                    transition.global_done,
                    transition.value,
                    transition.reward,
                )
                delta = reward + self.config["GAMMA"] * next_value * (1 - done) - value
                gae = (delta + self.config["GAMMA"] * self.config["GAE_LAMBDA"] * (1 - done) * gae)
                return (gae, value), gae

            _, advantages = jax.lax.scan(_get_advantages,
                                         (jnp.zeros_like(last_val), last_val),
                                         traj_batch,
                                         reverse=True,
                                         unroll=16,
                                         )
            return advantages, advantages + traj_batch.value

        advantages, targets = _calculate_gae(traj_batch, last_val)

        def _update_epoch(update_state, unused):
            def _update_minbatch(train_state, batch_info):
                init_hstate, traj_batch, advantages, targets = batch_info

                def _loss_fn(params, init_hstate, traj_batch, gae, targets):
                    # RERUN NETWORK
                    _, pi, value, _, _ = train_state.apply_fn(params,
                                                              init_hstate.squeeze(axis=0),
                                                              (traj_batch.obs,
                                                               traj_batch.done,
                                                               # traj_batch.avail_actions
                                                               ),
                                                              traj_batch.mem_state.th
                                                              )
                    log_prob = pi.log_prob(traj_batch.action)

                    # CALCULATE VALUE LOSS
                    value_pred_clipped = traj_batch.value + (value - traj_batch.value).clip(-self.config["CLIP_EPS"],
                                                                                            self.config["CLIP_EPS"])
                    value_losses = jnp.square(value - targets)
                    value_losses_clipped = jnp.square(value_pred_clipped - targets)
                    value_loss = 0.5 * jnp.maximum(value_losses, value_losses_clipped).mean(
                        where=(1 - traj_batch.done))

                    # CALCULATE ACTOR LOSS
                    ratio = jnp.exp(log_prob - traj_batch.log_prob)
                    gae = (gae - gae.mean()) / (gae.std() + 1e-8)
                    loss_actor1 = ratio * gae
                    loss_actor2 = (jnp.clip(ratio,
                                            1.0 - self.config["CLIP_EPS"],
                                            1.0 + self.config["CLIP_EPS"],
                                            ) * gae)
                    loss_actor = -jnp.minimum(loss_actor1, loss_actor2)
                    loss_actor = loss_actor.mean(where=(1 - traj_batch.done))
                    entropy = pi.entropy().mean(where=(1 - traj_batch.done))

                    total_loss = (loss_actor
                                  + self.config["VF_COEF"] * value_loss
                                  - self.config["ENT_COEF"] * entropy
                                  )

                    return total_loss, (value_loss, loss_actor, entropy)

                grad_fn = jax.value_and_grad(_loss_fn, has_aux=True)
                total_loss, grads = grad_fn(train_state.params, init_hstate, traj_batch, advantages, targets)
                train_state = train_state.apply_gradients(grads=grads)
                return train_state, total_loss

            train_state, mem_state, traj_batch, advantages, targets, key = update_state
            key, _key = jrandom.split(key)

            # adding an additional "fake" dimensionality to perform minibatching correctly
            init_hstate = jnp.reshape(mem_state.hstate, (1, self.config["NUM_ENVS"], -1))

            permutation = jrandom.permutation(_key, self.config["NUM_ENVS"])
            traj_batch = jax.tree_map(lambda x: jnp.swapaxes(x, 0, 1), traj_batch)
            batch = (init_hstate,  # TODO check this axis swapping etc if it works
                     traj_batch,
                     jnp.swapaxes(advantages, 0, 1).squeeze(),
                     jnp.swapaxes(targets, 0, 1).squeeze())
            shuffled_batch = jax.tree_util.tree_map(lambda x: jnp.take(x, permutation, axis=1), batch)
            minibatches = jax.tree_util.tree_map(lambda x: jnp.swapaxes(
                jnp.reshape(x, [x.shape[0], self.config["NUM_MINIBATCHES"], -1] + list(x.shape[2:]), ), 1, 0, ),
                                                 shuffled_batch, )

            train_state, total_loss = jax.lax.scan(_update_minbatch, train_state, minibatches)

            traj_batch = jax.tree_map(lambda x: jnp.swapaxes(x, 0, 1), traj_batch)  # TODO dodge to swap back again

            update_state = (train_state,
                            mem_state,
                            traj_batch,
                            advantages,
                            targets,
                            key,
                            )
            return update_state, total_loss

        update_state = (train_state, mem_state, traj_batch, advantages, targets, key)
        update_state, loss_info = jax.lax.scan(_update_epoch, update_state, None, self.config["UPDATE_EPOCHS"])
        train_state, mem_state, traj_batch, advantages, targets, key = update_state
        # TODO unsure if need to update the mem_state at all with the new hstate thingos

        return train_state, mem_state, env_state, ac_in, key

    @partial(jax.jit, static_argnums=(0, 3))
    def update_encoding(self, train_state, mem_state, agent, obs_batch, action, reward, done):
        return mem_state