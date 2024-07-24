from ml_collections import config_dict


def get_config():
    config = config_dict.ConfigDict()
    config.SEED = 42
    config.LR = 3e-4
    config.GAMMA = 0.995
    config.EPS = 1
    config.GRU_HIDDEN_DIM = 256
    config.GAE_LAMBDA = 0.95
    config.UPDATE_EPOCHS = 4
    config.NUM_MINIBATCHS = 4
    config.CLIP_EPS = 0.2
    config.VF_COEF = 0.5
    config.ENT_COEF = 0.01

    config.ANNEAL_LR = False
    config.MAX_GRAD_NORM = 0.5

    # config.TOTAL_TIMESTEPS = 10000000
    config.NUM_UPDATES = 10  # 10000
    config.NUM_INNER_STEPS = 16  # 128
    config.NUM_META_STEPS  = 16
    config.NUM_ENVS = 4
    config.NUM_DEVICES = 1

    config.NUM_ENSEMBLE = 10
    config.RP_NOISE = 0.1
    config.SIGMA_SCALE = 3.0

    config.TAU = 1.0

    config.AUTOTUNE = False
    config.TARGET_ENT_SCALE = 0.89
    config.ALPHA = 0.2

    config.BUFFER_SIZE = 100000
    config.LEARNING_STARTS = int(config.NUM_UPDATES * 0.2)  # starts policy after 20% of outer loops
    config.TARGET_NETWORK_FREQ = 4
    config.REPLAY_PRIORITY_EXP = 1.0
    config.IMPORTANCE_SAMPLING_EXP = 0.995

    config.BATCH_SIZE = 32

    config.WANDB = "disabled"  # "online" if want it to work
    # config.WANDB = "online"

    config.WANDB_ENTITY = "jamesr-j"  # change this to your wandb username

    config.AGENT_TYPE = ["PPO", "PPO"]
    config.NUM_AGENTS = 2  # TODO is this really the best way?

    return config