"""Math models registry. Models instantiated once at startup."""

import logging

logger = logging.getLogger(__name__)

_models = {}

# RF niches — all share one singleton instance
RF_NICHES = {'generic', 'sports_other', 'entertainment', 'tech', 'science', 'other'}


def get_model(niche: str):
    """Get or create a math model for the given niche."""
    # All RF niches share one singleton to avoid re-training
    if niche in RF_NICHES:
        if 'rf_singleton' in _models:
            return _models['rf_singleton']
        from core.math_models.rf_model import get_rf_model
        instance = get_rf_model()
        _models['rf_singleton'] = instance
        return instance
    if niche in _models:
        return _models[niche]

    MODEL_MAP = {
        'nba': ('core.math_models.elo_model', 'EloModel'),
        'f1': ('core.math_models.f1_model', 'F1Model'),
        'crypto': ('core.math_models.crypto_model', 'CryptoModel'),
        'geopolitics': ('core.math_models.geo_model', 'GeoModel'),
        'politics': ('core.math_models.politics_model', 'PoliticsModel'),
        'golf': ('core.math_models.golf_model', 'GolfModel'),
        # RF is the generic fallback — works for all niches without prior data
        'generic': ('core.math_models.rf_model', 'RFModel'),
        'sports_other': ('core.math_models.rf_model', 'RFModel'),
        'entertainment': ('core.math_models.rf_model', 'RFModel'),
        'tech': ('core.math_models.rf_model', 'RFModel'),
        'science': ('core.math_models.rf_model', 'RFModel'),
        'other': ('core.math_models.rf_model', 'RFModel'),
    }

    module_path, class_name = MODEL_MAP.get(niche, ('core.math_models.generic_model', 'GenericModel'))

    try:
        import importlib
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        _models[niche] = cls()
        logger.info(f"Instantiated math model: {niche} ({class_name})")
        return _models[niche]
    except Exception as e:
        logger.warning(f"Could not load model for {niche}: {e}. Falling back to GenericModel.")
        from core.math_models.generic_model import GenericModel
        _models[niche] = GenericModel()
        return _models[niche]
