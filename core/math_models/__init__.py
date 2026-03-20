"""Math models registry. Models instantiated once at startup."""

import logging

logger = logging.getLogger(__name__)

_models = {}

def get_model(niche: str):
    """Get or create a math model for the given niche."""
    if niche in _models:
        return _models[niche]

    MODEL_MAP = {
        'nba': ('core.math_models.elo_model', 'EloModel'),
        'f1': ('core.math_models.f1_model', 'F1Model'),
        'crypto': ('core.math_models.crypto_model', 'CryptoModel'),
        'geopolitics': ('core.math_models.geo_model', 'GeoModel'),
        'politics': ('core.math_models.politics_model', 'PoliticsModel'),
        'golf': ('core.math_models.golf_model', 'GolfModel'),
        # Generic fallback: GenericModel (momentum + API history)
        # RF disabled on Polymarket — no real trade history to train on
        'generic': ('core.math_models.generic_model', 'GenericModel'),
        'sports_other': ('core.math_models.generic_model', 'GenericModel'),
        'entertainment': ('core.math_models.generic_model', 'GenericModel'),
        'tech': ('core.math_models.generic_model', 'GenericModel'),
        'science': ('core.math_models.generic_model', 'GenericModel'),
        'other': ('core.math_models.generic_model', 'GenericModel'),
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
