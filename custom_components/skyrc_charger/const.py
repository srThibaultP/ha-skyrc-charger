"""Constants for the SkyRC Charger (MC5000) integration."""

from dataclasses import dataclass

DOMAIN = "skyrc_charger"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_MODEL = "model"

# The MC5000 expects the passcode shown on its screen, as the app sends it.
OPTION_PASSCODE = "passcode"
DEFAULT_PASSCODE = "1234"
# The password the app tries first on the older chargers.
DEFAULT_LEGACY_PASSWORD = "5793"

DEFAULT_NAME = "SkyRC Charger"

MODEL_BD380 = "bd380"
MODEL_MC5000 = "mc5000"
MODEL_MC3000 = "mc3000"
MODEL_NC2200 = "nc2200"
MODEL_NC2600 = "nc2600"
MODEL_PSU650 = "psu650"
MODEL_NC3000 = "nc3000"
MODEL_TYPE228 = "type228"


@dataclass(frozen=True, slots=True)
class LegacyModel:
    """One model of the list the SkyRC app downloads."""

    key: str
    name: str
    code: str  # the model code the charger answers to the info request
    channels: int
    password: bool  # the charger asks for its password


LEGACY_MODELS: tuple[LegacyModel, ...] = (
    LegacyModel("q200", "Q200", "100104", 4, True),
    LegacyModel("q200v2", "Q200V2", "100158", 4, True),
    LegacyModel("q200neo", "Q200 neo", "100197", 4, True),
    LegacyModel("q500neo", "Q500 neo", "100232", 4, True),
    LegacyModel("d100", "D100", "100131", 2, True),
    LegacyModel("d200s", "D200s", "100146", 2, True),
    LegacyModel("d200nex", "D200 Nex", "100196", 2, True),
    LegacyModel("d200neo_metal", "D200 neo (metal)", "100203", 2, False),
    LegacyModel("d260", "D260", "100175", 2, True),
    LegacyModel("d750", "D750 Ultra", "100201", 2, True),
    LegacyModel("d8neo", "D8neo", "100230", 2, True),
    LegacyModel("b6nano", "B6nano", "100134", 1, True),
    LegacyModel("b6evo", "B6evo", "100168", 1, False),
    LegacyModel("b6nax", "B6 nax", "100174", 1, True),
)
LEGACY_BY_KEY = {model.key: model for model in LEGACY_MODELS}


# Labels for the model picker in the config flow.
MODELS = {
    **{model.key: f"SkyRC {model.name} ({model.channels} channels)" for model in LEGACY_MODELS},
    MODEL_BD380: "SkyRC BD380 (discharger)",
    MODEL_MC3000: "SkyRC MC3000 (4 slots)",
    MODEL_MC5000: "SkyRC MC5000 (4 slots)",
    MODEL_NC2200: "SkyRC NC2200 (4 slots)",
    MODEL_NC2600: "SkyRC NC2600 (4 slots)",
    MODEL_PSU650: "SkyRC PSU650 (power supply)",
    MODEL_NC3000: "SkyRC NC3000 (4 slots)",
    MODEL_TYPE228: "SkyRC NC3000 Octa (8 slots)",
}

# What the charger answers to the info request, as the app tells them apart.
MODEL_BY_CODE = {
    **{model.code: model.key for model in LEGACY_MODELS},
    "600153": MODEL_BD380,
    "100214": MODEL_MC5000,
    "100181": MODEL_NC2200,
    "200031": MODEL_PSU650,
    "100209": MODEL_NC3000,
    "100228": MODEL_TYPE228,
}

# Default title of a config entry, which is also the device name entity ids
# are built from - so keep it short and free of parentheses.
MODEL_NAMES = {
    **{model.key: f"SkyRC {model.name}" for model in LEGACY_MODELS},
    MODEL_BD380: "SkyRC BD380",
    MODEL_MC3000: "SkyRC MC3000",
    MODEL_MC5000: "SkyRC MC5000",
    MODEL_NC2200: "SkyRC NC2200",
    MODEL_NC2600: "SkyRC NC2600",
    MODEL_PSU650: "SkyRC PSU650",
    MODEL_NC3000: "SkyRC NC3000",
    MODEL_TYPE228: "SkyRC NC3000 Octa",
}

CHANNELS_BY_MODEL = {
    **{model.key: model.channels for model in LEGACY_MODELS},
    MODEL_BD380: 1,
    MODEL_MC3000: 4,
    MODEL_MC5000: 4,
    MODEL_NC2200: 4,
    MODEL_NC2600: 4,
    MODEL_PSU650: 4,
    MODEL_NC3000: 4,
    MODEL_TYPE228: 8,
}

# Models whose slots can be started from Home Assistant, and which report
# error codes and a chemistry. The others are monitored and stopped only.
MODELS_WITH_START = {MODEL_MC5000}

# Models without a stop command: a power supply has nothing to stop, and
# the NC2600 app stops a slot by sending a program, which is not offered.
MODELS_WITHOUT_STOP = {MODEL_PSU650, MODEL_NC2600}

# Models that can give a slot's logged voltage curve.
MODELS_WITH_CURVE = {MODEL_MC3000, MODEL_MC5000, MODEL_NC3000, MODEL_TYPE228}

MAX_CHANNELS = max(CHANNELS_BY_MODEL.values())

# The MC5000 uses a generic BLE module so its name varies; those patterns are
# matched case-insensitively as a substring. Keep them reasonably specific:
# they are only used to *suggest* devices in the config flow, never to
# silently connect to whatever happens to be nearby.
MC5000_BLE_NAME_PATTERNS = ("mc5000", "skyrc", "#charger", "telinkse", "charger", "psu650")

# What the NC2600 gives in the info answer (the app tells its two
# variants apart by it); the MC3000 gives none.
NC2600_CODES = ("100113", "100145")

# The MC3000 gives no model code: the app tells it by its own protocol.

# The app tells the PSU650 by its Bluetooth name, not by a model code.
PSU650_NAME_FRAGMENT = "PSU650"

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


def default_passcode(model: str) -> str:
    """The factory passcode of a model, which the app tries first."""
    if model in LEGACY_BY_KEY or model in (MODEL_BD380, MODEL_PSU650):
        return DEFAULT_LEGACY_PASSWORD
    return DEFAULT_PASSCODE
