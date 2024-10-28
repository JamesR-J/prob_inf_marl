from ml_collections import config_dict


def get_VLITEMA_config():
    config = config_dict.ConfigDict()
    config.PRIOR_SCALE = 1.0  # 5.0  # 0.5
    config.LR = 1e-3
    config.ENS_LR = 1e-3
    config.OPP_ENS_LR = 1e-3
    config.TAU_LR = 1e-3  # 1e-2
    config.GAMMA = 0.99
    config.TD_LAMBDA = 0.8
    config.REWARD_NOISE_SCALE = 0.1  # set in ersac paper
    config.UNCERTAINTY_SCALE = 1.0
    config.MASK_PROB = 0.8  # 0.6
    config.HIDDEN_SIZE = 128
    config.NUM_ENSEMBLE = 10
    config.INIT_TAU = 0.02  # set in ersac paper

    return config