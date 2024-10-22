from absl import app
from project_name.baselines_run import run_train
import wandb
from project_name.config import get_config  # TODO dodge need to know how to fix this
import jax
from jax.lib import xla_bridge
import jax.profiler


def main(_):
    config = get_config()

    # TODO need to change update output to be for model info rather than env_state

    # TODO have changed PPO so double check it is correct to vanilla PPO and PPO_RNN and MFOS (as these are now correct)

    # TODO needs to add the v-trace at some point but kinda cba for now
    # TODO should be fairly easy using rlax if vmap the loss?

    wandb.init(project="ProbInfMarl",
        entity=config.WANDB_ENTITY,
        config=config,
        group="coin-game_tests",
        mode=config.WANDB
    )

    config.DEVICE = xla_bridge.get_backend().platform

    with jax.disable_jit(disable=config.DISABLE_JIT):
        train = jax.jit(run_train(config))
        out = jax.block_until_ready(train())  # .block_until_ready()
        jax.profiler.save_device_memory_profile("memory.prof")

    print("FINITO")


if __name__ == '__main__':
    app.run(main)
