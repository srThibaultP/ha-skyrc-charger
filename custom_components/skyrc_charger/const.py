"""Constants for the SkyRC Charger (MC5000) integration."""

DOMAIN = "skyrc_charger"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_MODEL = "model"

# The MC5000 expects the passcode shown on its screen, as the app sends it.
OPTION_PASSCODE = "passcode"
DEFAULT_PASSCODE = "1234"

DEFAULT_NAME = "SkyRC Charger"

MODEL_MC5000 = "mc5000"
MODEL_NC2200 = "nc2200"
MODEL_NC3000 = "nc3000"

# Labels for the model picker in the config flow.
MODELS = {
    MODEL_MC5000: "SkyRC MC5000 (4 slots)",
    MODEL_NC2200: "SkyRC NC2200 (4 slots)",
    MODEL_NC3000: "SkyRC NC3000 (4 slots)",
}

# What the charger answers to the info request, as the app tells them apart.
MODEL_BY_CODE = {
    "100214": MODEL_MC5000,
    "100181": MODEL_NC2200,
    "100209": MODEL_NC3000,
}

# Default title of a config entry, which is also the device name entity ids
# are built from - so keep it short and free of parentheses.
MODEL_NAMES = {
    MODEL_MC5000: "SkyRC MC5000",
    MODEL_NC2200: "SkyRC NC2200",
    MODEL_NC3000: "SkyRC NC3000",
}

CHANNELS_BY_MODEL = {
    MODEL_MC5000: 4,
    MODEL_NC2200: 4,
    MODEL_NC3000: 4,
}

# Models whose slots can be started from Home Assistant, and which report
# error codes and a chemistry. The others are monitored and stopped only.
MODELS_WITH_START = {MODEL_MC5000}

# Models that can give a slot's logged voltage curve.
MODELS_WITH_CURVE = {MODEL_MC5000, MODEL_NC3000}

MAX_CHANNELS = max(CHANNELS_BY_MODEL.values())

# The MC5000 uses a generic BLE module so its name varies; those patterns are
# matched case-insensitively as a substring. Keep them reasonably specific:
# they are only used to *suggest* devices in the config flow, never to
# silently connect to whatever happens to be nearby.
MC5000_BLE_NAME_PATTERNS = ("mc5000", "skyrc", "#charger", "telinkse", "charger")

# The charger speaks over a TelinkSE-style serial service.
BLE_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
BLE_CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# The chemistry names mirror the byte values the charger reports.
MC5000_CHEMISTRY_OPTIONS = [
    "any",
    "liion",
    "liion_hv",
    "lifepo4",
    "nimh",
    "nicd",
    "eneloop",
    "nizn",
    "ram",
    "lto",
    "naion",
]

CHEMISTRY_OPTIONS_BY_MODEL = {
    MODEL_MC5000: MC5000_CHEMISTRY_OPTIONS,
}

# For service schemas, which are registered once for the whole integration.
CHEMISTRY_OPTIONS = sorted(MC5000_CHEMISTRY_OPTIONS)

MC5000_MODE_OPTIONS = ["charge", "storage", "discharge", "cycle", "refresh", "break_in"]

MODE_OPTIONS = sorted(MC5000_MODE_OPTIONS)
