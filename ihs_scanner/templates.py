"""D26 V4 templates: the user's five golden examples, normalised (GENERATED -- do not edit by hand).

Regenerate: scripts/build_templates.py (reads tests/golden_examples.py + tests/fixtures/).
y = (LS, N1, N2, RS): y'_k = (log P_k - log H.low) / (log neck(H) - log H.low); H = 0, neckline above H = 1.
x = (N1, H, N2):      x'_k = (bar_k - bar_LS) / (bar_RS - bar_LS); LS = 0, RS = 1.
"""

TEMPLATES = {
    "HMSTRUSDT_binance_spot_4h": {"y": (0.3199, 1.0842, 0.9577, 0.1874), "x": (0.0347, 0.6089, 0.8911)},
    "TRUSTUSDT_binance_usdm_4h": {"y": (0.5845, 1.0302, 0.7973, 0.3404), "x": (0.0085, 0.1344, 0.9274)},
    "GRAM-USDT_okx_spot_1d": {"y": (0.4755, 1.1911, 0.9706, 0.3136), "x": (0.0404, 0.7879, 0.8990)},
    "AZTECUSDT_binance_usdm_1d": {"y": (0.7062, 1.2532, 0.8569, 0.2859), "x": (0.0426, 0.6383, 0.9291)},
    "PENDLEUSDT_binance_spot_1d": {"y": (0.6829, 1.0139, 0.9815, 0.6756), "x": (0.0218, 0.4206, 0.9408)},
}
