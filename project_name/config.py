from ml_collections import config_dict


def get_config():
    # PPO and MFOS
    config = config_dict.ConfigDict()
    config.SEED = 42

    # config.CNN = False
    config.CNN = True

    # config.TOTAL_TIMESTEPS = 10000000
    config.NUM_INNER_STEPS = 30  # 16  # ep rollout length
    config.NUM_META_STEPS = 40000  # 100  # 500  # number of ep rollouts to run
    config.NUM_UPDATES = 1  # 500  # 500  # number of meta rollouts, should be 1 for no meta training
    config.NUM_ENVS = 1
    config.NUM_DEVICES = 1

    # config.DEEP_SEA_MAP = 1  # 20

    # config.WANDB = "disabled"  # "online" if want it to work
    config.WANDB = "online"

    config.DISABLE_JIT = False
    # config.DISABLE_JIT = True

    config.WANDB_ENTITY = "jamesr-j"  # change this to your wandb username

    config.AGENT_TYPE = ["VLITE"]  # , "PPO_RNN"]  # ["MFOS", "ERSAC"]  # ["ROMMEO", "ROMMEO"]
    config.NUM_AGENTS = 1  # TODO is this really the best way?

    config.CTDE = False
    # config.CTDE = True

    return config
