# -*- coding: utf-8 -*-
"""
Maritime Vessel Taxonomy and Visual Palette.
Defines the 9 fine-grained marine perception classes and high-contrast BGR colors.
"""

VESSEL_CLASSES = {
    0: "Fishing Boat / Trawler",
    1: "Cargo / Merchant Ship",
    2: "Container Ship",
    3: "Passenger Ship / Ferry",
    4: "Speedboat / Jet Ski",
    5: "Sailboat / Yacht",
    6: "Small Boat",
    7: "Person",
    8: "Life Vest / Floater / Buoy"
}

# Distinct High-Contrast BGR Color Palette
VESSEL_PALETTE = {
    0: (255, 255, 0),    # Cyan / Electric Blue (Fishing Boat)
    1: (255, 0, 255),    # Bright Magenta (Cargo Ship)
    2: (180, 0, 255),    # Deep Purple (Container Ship)
    3: (255, 128, 0),    # Royal Blue (Passenger Ship)
    4: (147, 20, 255),   # Neon Pink / Violet (Speedboat / Jet Ski)
    5: (64, 224, 208),   # Turquoise (Sailboat / Yacht)
    6: (50, 205, 50),    # Lime-Green (Small Boat)
    7: (0, 255, 0),      # Bright Green (Person)
    8: (0, 165, 255)     # Vibrant Orange (Life Vest / Floater)
}
