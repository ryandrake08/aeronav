 #!/usr/bin/env python3

datasets = {
    "Alaska Wall Planning Chart British Columbia Coast Inset": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (14778, 170, 2918, 7417),
        "gcps": [(14830, 356, -135, 55), (16322, 1481, -130, 55), (15198, 3098, -130, 52), (16905, 4174, -125, 52), (16239, 5304, -125, 50)],
    },
    "Alaska Wall Planning Chart East": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (81, 101, 17684, 12284),
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "Alaska Wall Planning Chart West": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (81, 101, 17684, 12284),
        "masks": [ [(1371,4970), (2875,4969), (2875,7972), (1371,7973)], [(2928,6658), (4278,6658), (4537,7445), (4535,7513), (2928,7513)], [(14691,101), (17765,101), (17765,7674), (14691,7674)], [(8873,10432), (15041,10432), (15041,12385), (8873,12385)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "Alaska Wall Planning Chart Western Aleutian Islands Inset East": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (8961, 10521, 5993, 1793),
        "geobound": (None, None, 180, None),
        "gcps": [(9321, 10705, 173, 53), (10099, 10772, 175, 53), (10880, 10818, 177, 53), (11661, 10840, 179, 53), (12444, 10840, -179, 53), (13225, 10818, -177, 53), (14005, 10773, -175, 53), (14784, 10705, -173, 53), (9190, 11995, 173, 51), (10006, 12066, 175, 51), (10823, 12113, 177, 51), (11642, 12136, 179, 51), (12462, 12136, -179, 51), (13281, 12113, -177, 51), (14098, 12066, -175, 51), (14914, 11995, -173, 51)],
        "antimeridian": True,
    },
    "Alaska Wall Planning Chart Western Aleutian Islands Inset West": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (8961, 10521, 5993, 1793),
        "geobound": (-180, None, None, None),
        "gcps": [(9321, 10705, 173, 53), (10099, 10772, 175, 53), (10880, 10818, 177, 53), (11661, 10840, 179, 53), (12444, 10840, -179, 53), (13225, 10818, -177, 53), (14005, 10773, -175, 53), (14784, 10705, -173, 53), (9190, 11995, 173, 51), (10006, 12066, 175, 51), (10823, 12113, 177, 51), (11642, 12136, 179, 51), (12462, 12136, -179, 51), (13281, 12113, -177, 51), (14098, 12066, -175, 51), (14914, 11995, -173, 51)],
        "antimeridian": True,
    },
    "Albuquerque SEC": {
        "window": (2202, 34, 15698, 11308),
        "geobound": (-109, 32, None, None),
    },
    "Anchorage SEC": {
        "window": (1743, 61, 14877, 11160),
        "geobound": (-151.5, 60, None, None),
    },
    "Anchorage TAC": {
        "window": (3158, 97, 8881, 5810),
    },
    "Atlanta FLY": {
        "window": (1630, 188, 7419, 6960),
    },
    "Atlanta SEC": {
        "window": (2200, 934, 15723, 11176),
        "geobound": (-88, 32, None, None),
    },
    "Atlanta TAC": {
        "window": (1546, 555, 7421, 6962),
    },
    "Baltimore HEL": {
        "window": (3561, 526, 5932, 5544),
    },
    "Baltimore-Washington FLY": {
        "window": (1614, 187, 10413, 8414),
    },
    "Baltimore-Washington TAC": {
        "window": (1612, 184, 10410, 8414),
    },
    "Bethel SEC": {
        "window": (1073, 31, 15593, 12042),
        "geobound": (-173, None, None, None),
    },
    "Billings SEC": {
        "window": (2169, 6, 15701, 12101),
        "geobound": (-109, None, None, None),
    },
    "Boston Downtown HEL": {
        "window": (1549, 293, 7404, 8242),
    },
    "Boston HEL": {
        "window": (1528, 299, 7406, 8242),
    },
    "Boston TAC": {
        "window": (1614, 316, 8908, 8456),
    },
    "Brownsville SEC": {
        "window": (1457, 4, 15145, 11301),
        "geobound": (-103, 24, None, None),
    },
    "Cape Lisburne SEC": {
        "window": (1974, 12, 14586, 11417),
        "geobound": (-171.5, 68, None, None),
    },
    "Caribbean 1 VFR Chart": {
        "window": (1763, 0, 16190, 15240),
        "geobound": (None, 16, None, None),
    },
    "Caribbean 2 VFR Chart": {
        "window": (1975, 0, 15839, 11003),
        "geobound": (None, 14, None, None),
    },
    "Charlotte FLY": {
        "window": (1642, 240, 7401, 6865),
    },
    "Charlotte SEC": {
        "window": (1358, 40, 15342, 11275),
        "geobound": (-82, 32, None, None),
    },
    "Charlotte TAC": {
        "window": (1563, 519, 7401, 6864),
    },
    "Cheyenne SEC": {
        "window": (1620, 0, 16299, 12139),
        "geobound": (-109, 40, None, None),
    },
    "Chicago FLY": {
        "window": (1535, 145, 5898, 5386),
    },
    "Chicago HEL": {
        "window": (1529, 58, 11906, 8812),
    },
    "Chicago O'Hare Inset HEL": {
        "window": (145, 177, 3541, 3883),
    },
    "Chicago SEC": {
        "window": (1640, 841, 16284, 11219),
        "geobound": (-93, 40, None, None),
    },
    "Chicago TAC": {
        "window": (1546, 536, 5897, 5386),
    },
    "Cincinnati FLY": {
        "window": (1549, 147, 7407, 8315),
    },
    "Cincinnati SEC": {
        "window": (2850, 6, 15060, 11364),
        "geobound": (-85, 36, None, None),
    },
    "Cincinnati TAC": {
        "window": (1551, 316, 7402, 8314),
    },
    "Cleveland TAC": {
        "window": (1669, 565, 5909, 5391),
    },
    "Cold Bay SEC": {
        "window": (1623, 4, 14937, 6083),
        "geobound": (-164, None, None, None),
    },
    "Colorado Springs TAC": {
        "window": (3189, 59, 8240, 7645),
    },
    "Dallas-Ft Worth FLY": {
        "window": (1608, 127, 10277, 8468),
    },
    "Dallas-Ft Worth HEL": {
        "window": (1524, 122, 11857, 8713),
    },
    "Dallas-Ft Worth SEC": {
        "window": (2210, 7, 15712, 11306),
        "geobound": (-102, 32, None, None),
    },
    "Dallas-Ft Worth TAC": {
        "window": (1607, 127, 10277, 8468),
    },
    "Dallas-Love Inset HEL": {
        "window": (1992, 371, 5515, 5573),
    },
    "Dawson SEC": {
        "window": (1470, 0, 15113, 11197),
        "geobound": (-145, 64, None, None),
    },
    "Denver FLY": {
        "window": (1597, 325, 7408, 6896),
    },
    "Denver SEC": {
        "window": (1501, 16, 15114, 11854),
        "geobound": (-111, None, None, None),
    },
    "Denver TAC": {
        "window": (1543, 320, 7409, 6896),
    },
    "Detroit FLY": {
        "window": (3071, 232, 7342, 6402),
    },
    "Detroit HEL": {
        "window": (3275, 63, 10156, 8781),
    },
    "Detroit SEC": {
        "window": (1622, 0, 16303, 11336),
        "geobound": (-85, 40, None, None),
    },
    "Detroit TAC": {
        "window": (3082, 84, 7340, 6402),
    },
    "Downtown Manhattan HEL": {
        "window": (1578, 232, 4271, 3958),
    },
    "Dutch Harbor SEC": {
        "window": (1340, 8, 15233, 11283),
        "masks": [ [(5048,351), (7428,351), (7428,4041), (5048,4041)], [(10193,332), (13652,332), (13652,3541), (10193,3541)] ],
        "geobound": (-173, 52, None, None),
    },
    "Dutch Harbor SEC Dutch Harbor Inset": {
        "input_file": "Dutch Harbor SEC.tif",
        "window": (10288, 427, 3268, 3018),
        "gcps": [(10356, 470, -167, 54.25), (11896, 475, -166.5, 54.25), (13435, 470, -166, 54.25), (10347, 1785, -167, 54), (11896, 1790, -166.5, 54), (13444, 1785, -166, 54), (10337, 3099, -167, 53.75), (11896, 3104, -166.5, 53.75), (13454, 3099, -166, 53.75)],
    },
    "Dutch Harbor SEC Pribilof Islands Inset": {
        "input_file": "Dutch Harbor SEC.tif",
        "window": (5142, 445, 2192, 3500),
        "gcps": [(5411, 884, -170.5, 57.5), (6119, 886, -170, 57.5), (6827, 884, -169.5, 57.5), (5401, 2199, -170.5, 57), (6119, 2202, -170, 57), (6836, 2199, -169.5, 57), (5391, 3515, -170.5, 56.5), (6119, 3518, -170, 56.5), (6848, 3515, -169.5, 56.5)],
    },
    "Eastern Long Island HEL": {
        "window": (3267, 220, 6893, 3906),
    },
    "El Paso SEC": {
        "window": (1669, 12, 14876, 11273),
        "geobound": (-109, 28, None, None),
    },
    "ENR_A01_ATL": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_DCA": {
        "window": (125, 205, 9751, 7591),
    },
    "ENR_A01_DET": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_JAX": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A01_MIA": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_MSP": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A01_STL": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_DEN": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_DFW": {
        "window": (125, 205, 5751, 7591),
    },
    "ENR_A02_LAX": {
        "window": (125, 145, 7751, 3671),
    },
    "ENR_A02_MKC": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A02_ORD": {
        "window": (125, 205, 3751, 7591),
    },
    "ENR_A02_PHX": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_SFO": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_AKH01": {
        "window": (2200, 263, 19590, 7471),
        "masks": [ [(12856,4274), (21790,4272), (21790,7735), (12856,7735)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "ENR_AKH01_SEA": {
        "window": (128, 127, 8728, 3250),
    },
    "ENR_AKH02 East": {
        "input_file": "ENR_AKH02.tif",
        "window": (2205, 265, 19591, 7471),
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "ENR_AKH02 West": {
        "input_file": "ENR_AKH02.tif",
        "window": (2205, 265, 19591, 7471),
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "ENR_AKL01": {
        "window": (2205, 274, 19583, 7461),
        "masks": [ [(7743,4020), (12261,4021), (12261,7735), (7743,7735)], [(15188,4216), (21788,4216), (21788,7735), (15187,7735)] ],
    },
    "ENR_AKL01_JNU": {
        "window": (85, 153, 4171, 3531),
    },
    "ENR_AKL01_VR": {
        "window": (109, 145, 6431, 3349),
    },
    "ENR_AKL02C": {
        "window": (89, 105, 4448, 7470),
    },
    "ENR_AKL02E": {
        "window": (89, 105, 4450, 7471),
    },
    "ENR_AKL02W East": {
        "input_file": "ENR_AKL02W.tif",
        "window": (96, 111, 4466, 7465),
        "masks": [ [(96,111), (1402,111), (1402,4019), (96,4019)] ],
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "ENR_AKL02W West": {
        "input_file": "ENR_AKL02W.tif",
        "window": (96, 111, 4466, 7465),
        "masks": [ [(96,111), (1402,111), (1402,4019), (96,4019)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "ENR_AKL03": {
        "window": (6085, 265, 15711, 7470),
        "masks": [ [(6085,265), (7485,265), (7485,4084), (6085,4084)] ],
    },
    "ENR_AKL03_FAI": {
        "window": (86, 105, 5110, 3649),
    },
    "ENR_AKL03_OME": {
        "window": (85, 85, 3711, 3651),
    },
    "ENR_AKL04 West": {
        "input_file": "ENR_AKL04.tif",
        "window": (6529, 269, 15262, 7463),
        "masks": [ [(6529,269), (10102,269), (10102,3810), (8947,3810), (6529,7239)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "ENR_AKL04_ANC": {
        "window": (209, 269, 7713, 7462),
        "masks": [ [(6853,3631), (7922,3630), (7922,7730), (4351,7730), (4351,7183)] ],
    },
    "ENR_CA01_ANTIGUA": {
        "window": (22, 124, 5695, 2621),
    },
    "ENR_CA01_BUENOS": {
        "window": (77, 130, 2695, 2615),
    },
    "ENR_CA01_GUAT": {
        "window": (68, 126, 3409, 2623),
    },
    "ENR_CA01_LIMA": {
        "window": (96, 130, 2695, 2621),
    },
    "ENR_CA01_RIO": {
        "window": (113, 130, 2695, 2617),
    },
    "ENR_CA01_SANTIAGO": {
        "window": (122, 130, 2695, 2620),
    },
    "ENR_CA02_BOGOTA": {
        "window": (123, 100, 2695, 2621),
    },
    "ENR_CA02_CENT-PAC": {
        "window": (105, 100, 4189, 5583),
    },
    "ENR_CA02_MEX": {
        "window": (100, 123, 2695, 2624),
    },
    "ENR_CA02_MIA-NAS": {
        "window": (92, 146, 4177, 2622),
    },
    "ENR_CA02_PANAMA": {
        "window": (68, 105, 4177, 2642),
    },
    "ENR_CA03_MEX_BORDER": {
        "window": (102, 126, 2799, 5497),
    },
    "ENR_CA03_PR": {
        "window": (77, 176, 8696, 5507),
    },
    "ENR_CH01": {
        "window": (1652, 184, 11695, 5619),
    },
    "ENR_CH02": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CH07": {
        "window": (1652, 184, 11695, 5619),
    },
    "ENR_CH08": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CL01": {
        "window": (1652, 189, 11695, 5614),
    },
    "ENR_CL02": {
        "window": (1652, 173, 11695, 5630),
    },
    "ENR_CL03": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CL05": {
        "window": (1652, 197, 11695, 5604),
        "masks": [ [(8599,199), (13347,199), (13347,2262), (10095,2262), (10095,2093), (8599,1293)] ],
    },
    "ENR_CL05 Charleston-Bermuda Inset": {
        "input_file": "ENR_CL05.tif",
        "window": (8702, 200, 4644, 1985),
        "masks": [ [(8702,200), (13346,200), (13346,2185), (10190,2185), (10190,2056), (8702,1251)] ],
    },
    "ENR_CL06": {
        "window": (1652, 182, 11695, 5640),
    },
    "ENR_H01": {
        "window": (2204, 205, 21691, 7590),
    },
    "ENR_H02": {
        "window": (2105, 205, 19791, 7591),
    },
    "ENR_H03": {
        "window": (2206, 105, 21690, 7691),
    },
    "ENR_H04": {
        "window": (204, 105, 21691, 7630),
        "masks": [ [(204,3054), (2364,3054), (2364,7735), (204,7735)] ],
    },
    "ENR_H05": {
        "window": (2105, 105, 19791, 7689),
    },
    "ENR_H06": {
        "window": (2105, 105, 19791, 7689),
    },
    "ENR_H07": {
        "window": (2205, 105, 19690, 7631),
    },
    "ENR_H08": {
        "window": (2105, 105, 19791, 7631),
    },
    "ENR_H09": {
        "window": (2205, 105, 19691, 7691),
    },
    "ENR_H10": {
        "window": (2105, 105, 19691, 7691),
    },
    "ENR_H11": {
        "window": (4105, 205, 17691, 7591),
    },
    "ENR_H12": {
        "window": (104, 105, 23791, 7691),
    },
    "ENR_L01": {
        "window": (2204, 265, 19588, 7469),
    },
    "ENR_L02": {
        "window": (2206, 266, 17589, 7470),
    },
    "ENR_L03": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L04": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L05": {
        "window": (6205, 264, 15591, 7471),
    },
    "ENR_L06N": {
        "window": (205, 264, 13591, 7471),
    },
    "ENR_L06S": {
        "window": (205, 264, 7591, 7471),
    },
    "ENR_L07": {
        "window": (2204, 265, 19591, 7471),
    },
    "ENR_L08": {
        "window": (2204, 265, 17591, 7471),
    },
    "ENR_L09": {
        "window": (2205, 265, 19589, 7471),
    },
    "ENR_L10": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L11": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L12": {
        "window": (4203, 263, 17594, 7472),
    },
    "ENR_L13": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L14": {
        "window": (4205, 265, 17589, 7471),
    },
    "ENR_L15": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L16": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L17": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L18": {
        "window": (4205, 265, 19591, 7471),
    },
    "ENR_L19": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L20": {
        "window": (2205, 265, 17591, 7471),
    },
    "ENR_L21": {
        "window": (2205, 265, 15591, 7471),
    },
    "ENR_L22": {
        "window": (205, 265, 21591, 7471),
    },
    "ENR_L23": {
        "window": (5185, 265, 16611, 7471),
    },
    "ENR_L23 Wilmington-Bimini Inset": {
        "input_file": "ENR_L23.tif",
        "window": (2205, 265, 2769, 7471),
        "gcps": [(2398, 461, -81, 35), (3655, 291, -79, 35), (4401, 958, -78, 34), (2683, 2760, -81, 32), (3337, 2675, -80, 32), (4643, 2484, -78, 32), (2552, 7488, -82, 26), (3963, 7312, -80, 26), (4667, 7213, -79, 26)],
    },
    "ENR_L24": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L25": {
        "window": (2204, 265, 17591, 7471),
    },
    "ENR_L26": {
        "window": (204, 262, 19591, 7474),
    },
    "ENR_L27": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L28": {
        "window": (2205, 265, 17591, 7471),
    },
    "ENR_L29": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L30": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L31": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L32": {
        "window": (4205, 265, 19591, 7471),
    },
    "ENR_L33": {
        "window": (2206, 265, 15590, 7466),
    },
    "ENR_L34": {
        "window": (205, 260, 23591, 7472),
        "masks": [ [(21595,260), (23796,260), (23796,4641), (22344,4254), (21738,3121)] ],
    },
    "ENR_L34 Boston-Yarmouth Inset": {
        "input_file": "ENR_L34.tif",
        "window": (21827, 274, 1971, 4151),
        "masks": [ [(21827,274), (21969,3082), (22502,4080), (23798,4425), (21827,4425)] ],
        "gcps": [(22662, 874, -70, 42), (23666, 1207, -70, 43), (22436, 1608, -69, 42), (23424, 1932, -69, 43), (21969, 3082, -67, 42), (22965, 3387, -67, 43)],
    },
    "ENR_L35": {
        "window": (2205, 265, 15589, 7466),
    },
    "ENR_L36": {
        "window": (205, 265, 21590, 7466),
    },
    "ENR_P01 East": {
        "input_file": "ENR_P01.tif",
        "window": (2204, 254, 15587, 7479),
        "masks": [ [(2204,4765), (5161,4765), (5161,6436), (7001,6436), (7001,7733), (2204,7733)] ],
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "ENR_P01 West": {
        "input_file": "ENR_P01.tif",
        "window": (2204, 254, 15587, 7479),
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "ENR_P01_GUA": {
        "window": (103, 107, 2794, 2794),
    },
    "ENR_P02": {
        "window": (204, 265, 17591, 7471),
    },
    "Fairbanks SEC": {
        "window": (1510, 33, 15116, 11427),
        "geobound": (-158, 64, None, None),
    },
    "Fairbanks TAC": {
        "window": (3103, 59, 8072, 5704),
    },
    "GOM_CN": {
        "window": (1754, 75, 13922, 12451),
    },
    "GOM_CS": {
        "window": (74, 74, 15601, 8100),
    },
    "GOM_WN": {
        "window": (74, 74, 15602, 12452),
        "masks": [ [(74,74), (3504,74), (3504,5986), (74,5986)] ],
    },
    "GOM_WS": {
        "window": (74, 74, 15603, 8136),
    },
    "Grand Canyon Air Tour Operators": {
        "window": (1646, 536, 11910, 5364),
        "masks": [ [(12117,536), (13556,536), (13556,2067), (12114,2070)] ],
    },
    "Grand Canyon General Aviation": {
        "window": (3111, 564, 11910, 5364),
        "masks": [ [(13576,564), (15021,564), (15021,2096), (13578,2099)] ],
    },
    "Grand Canyon General Aviation Marble Canyon Inset": {
        "input_file": "Grand Canyon General Aviation.tif",
        "window": (13653, 640, 1294, 1374),
        "gcps": [(14067, 978, -111.75, 36.833), (13719, 1415, -111.833, 36.75), (14068, 1413, -111.75, 36.75), (14419, 1412, -111.666, 36.75), (14769, 1411, -111.583, 36.75), (14070, 1848, -111.75, 36.666)],
    },
    "Great Falls SEC": {
        "window": (2170, 15, 15700, 12080),
        "geobound": (-117, None, None, None),
    },
    "Green Bay SEC": {
        "window": (2121, 32, 15788, 11668),
        "geobound": (-93, 44, None, None),
    },
    "Halifax SEC": {
        "window": (1338, 0, 15260, 12258),
        "geobound": (-69, 44, None, None),
    },
    "Halifax SEC Yarmouth Extension": {
        "input_file": "Halifax SEC.tif",
        "window": (6239, 11250, 1716, 988),
        "geobound": (-66.4, None, -65.5, 44),
    },
    "Hawaiian Islands SEC": {
        "window": (1845, 1009, 16760, 13718),
        "masks": [ [(1845,1009), (4862,1009), (1845,5508)], [(4862,1009), (18605,1009), (18605,10228)], [(18605,10228), (18605,14727), (15588,14727)], [(1845,5508),(15588,14727),  (1845,14727)] ],
    },
    "Honolulu Inset SEC": {
        "window": (68, 47, 5561, 4427),
        "masks": [ [(5629,3216), (5629,4474), (4200,4474)] ],
    },
    "Houston FLY": {
        "window": (1537, 497, 7441, 7467),
    },
    "Houston North HEL": {
        "window": (1792, 74, 8555, 7190),
    },
    "Houston SEC": {
        "window": (1673, 23, 14878, 11266),
        "geobound": (-97, 28, None, None),
    },
    "Houston South HEL": {
        "window": (1788, 75, 8557, 7421),
    },
    "Houston TAC": {
        "window": (1537, 497, 7441, 7468),
    },
    "Jacksonville SEC": {
        "window": (1698, 0, 14919, 11315),
        "masks": [ [(13614,0), (16617,0), (16617,3477), (13614,3477)], [(2250,7804), (4651,7804), (4651,10641), (2250,10641)] ],
        "geobound": (-85, 28, None, None),
    },
    "Jacksonville SEC Jacksonville Inset": {
        "input_file": "Jacksonville SEC.tif",
        "window": (13677, 6, 2916, 3346),
        "gcps": [(14794, 270, -81.75, 30.666), (15925, 270, -81.5, 30.666), (14038, 1142, -81.916, 30.5), (14794, 1142, -81.75, 30.5), (15927, 1142, -81.5, 30.5), (14026, 2451, -81.916, 30.25), (14793, 2452, -81.75, 30.25), (15929, 2451, -81.5, 30.25)],
    },
    "Juneau SEC": {
        "window": (871, 59, 15637, 11301),
        "masks": [ [(871,4217), (1460,4217), (5051,1460), (871,5051)], [(1588,2705), (4447,2705), (4447,3958), (1588,3958)], [(1616,4018), (4227,4018), (4227,5859), (1616,5859)] ],
        "geobound": (-141, 56, None, None),
    },
    "Juneau SEC Juneau Inset": {
        "input_file": "Juneau SEC.tif",
        "window": (1695, 4107, 2452, 1659),
        "gcps": [(2294, 4536, -134.75, 58.416), (2984, 4527, -134.5, 58.416), (3674, 4516, -134.25, 58.416), (2303, 5412, -134.75, 58.25), (2297, 5404, -134.5, 58.25), (3690, 5392, -134.25, 58.25)],
    },
    "Juneau SEC Seward Glacier Area Inset": {
        "input_file": "Juneau SEC.tif",
        "window": (1686, 2802, 2668, 2964),
        "geobound": (None, 60, None, None),
        "gcps": [(1699, 2938, -141, 60.333), (3002, 2944, -140, 60.333), (4306, 2931, -139, 60.333), (1688, 3814, -141, 60), (3005, 3820, -140, 60), (4322, 3805, -139, 60)],
    },
    "Kansas City SEC": {
        "window": (1518, 39, 15065, 11296),
        "geobound": (-97, 36, None, None),
    },
    "Kansas City TAC": {
        "window": (1554, 728, 7400, 6576),
    },
    "Ketchikan SEC": {
        "window": (1522, 21, 14987, 11246),
        "masks": [ [(4648,912), (7226,912), (7226,4425), (4648,4425)] ],
        "geobound": (-139, 52, None, None),
    },
    "Ketchikan SEC Ketchikan Inset": {
        "input_file": "Ketchikan SEC.tif",
        "window": (4730, 1007, 2415, 3322),
        "gcps": [(4844, 1615, -132, 55.5), (5590, 1618, -131.75, 55.5), (7083, 1617, -131.25, 55.5), (4836, 2930, -132, 55.25), (6337, 2933, -131.5, 55.25), (7089, 2931, -131.25, 55.25), (4873, 4245, -132, 55), (5584, 4248, -131.75, 55), (7095, 4246, -131.25, 55)],
    },
    "Klamath Falls SEC": {
        "window": (1628, 0, 16278, 12150),
        "geobound": (-125, 40, None, None),
    },
    "Kodiak SEC": {
        "window": (945, 5, 15651, 11358),
        "masks": [ [(13777,8497), (16467,8497), (16488,11363), (13777,11363)], [(945,4266), (1479,4266), (1479,5100), (945,5100)] ],
        "geobound": (-162, 56, None, None),
    },
    "Kodiak SEC Cape Newenham": {
        "input_file": "Kodiak SEC.tif",
        "window": (1218, 3755, 303, 426),
        "geobound": (None, None, -162, None),
    },
    "Kodiak SEC Carter Spit": {
        "input_file": "Kodiak SEC.tif",
        "window": (1540, 2212, 100, 441),
        "geobound": (None, None, -162, None),
    },
    "Kodiak SEC Kodiak Inset": {
        "input_file": "Kodiak SEC.tif",
        "window": (1218, 3755, 15240, 8237),
        "geobound": (None, None, -152, None),
        "gcps": [(14342, 8840, -152.75, 58), (15040, 8840, -152.5, 58), (15740, 8840, -152.25, 58), (14337, 10154, -152.75, 57.75), (15743, 10154, -152.25, 57.75), (14332, 11470, -152.75, 57.5), (15040, 11471, -152.5, 57.5), (15748, 11470, -152.25, 57.5)],
    },
    "Lake Huron SEC": {
        "window": (2116, 28, 15754, 11375),
        "geobound": (-85, 44, None, None),
    },
    "Las Vegas FLY": {
        "window": (3114, 225, 7378, 5389),
    },
    "Las Vegas SEC": {
        "window": (2696, 0, 15153, 11757),
        "geobound": (-118, None, None, None),
    },
    "Las Vegas TAC": {
        "window": (3100, 596, 7378, 5388),
    },
    "Los Angeles East HEL": {
        "window": (1553, 96, 9357, 8743),
    },
    "Los Angeles FLY": {
        "window": (1608, 121, 10206, 5785),
    },
    "Los Angeles SEC": {
        "window": (535, 1373, 16110, 10976),
        "masks": [ [(535,1373), (1591,1373), (1582,6255), (535,6256)], [(525,8082), (4688,8087), (4688,12349), (535,12349)] ],
        "geobound": (-122, 32, None, None),
    },
    "Los Angeles TAC": {
        "window": (4672, 105, 10205, 5785),
    },
    "Los Angeles West HEL": {
        "window": (1531, 87, 10388, 8736),
    },
    "Mariana Islands Inset SEC": {
        "window": (27, 34, 6125, 5974),
        "masks": [ [(27,34), (3689,34), (3687,3235), (2116,4618), (27,4618)] ],
    },
    "McGrath SEC": {
        "window": (1710, 0, 14787, 11374),
        "geobound": (-162, 60, None, None),
    },
    "Memphis SEC": {
        "window": (2181, 53, 15713, 11298),
        "geobound": (-95, 32, None, None),
    },
    "Memphis TAC": {
        "window": (1673, 556, 7441, 6821),
    },
    "Miami FLY": {
        "window": (1575, 73, 8885, 8777),
        "masks": [ [(7282,6938), (10404,6938), (10404,8799), (6359,8799), (6359,7324), (7282,7324)] ],
    },
    "Miami FLY Florida Keys Inset": {
        "input_file": "Miami FLY.tif",
        "window": (6427, 7000, 3914, 1734),
        "masks": [ [(6427,7000), (7344,7000), (7344,7386), (6427,7386)] ],
        "gcps": [(7687, 7329, -81.5, 25), (8879, 7323, -81, 25), (10073, 7311, -80.5, 25), (6494, 8640, -82, 24.5), (7692, 8638, -81.5, 24.5), (8890, 8631, -81, 24.5), (10089, 8619, -80.5, 24.5)],
    },
    "Miami SEC": {
        "window": (1497, 0, 15562, 12018),
        "masks": [ [(4107,0), (17059,0), (17059,1051), (4089,1041)] ],
        "geobound": (-83, 24, None, 28.5),
    },
    "Miami TAC": {
        "window": (1575, 72, 8890, 8783),
        "masks": [ [(7285,6943), (10408,6944), (10408,8804), (6361,8803), (6361,7329), (7285,7329)] ],
    },
    "Miami TAC Florida Keys Inset": {
        "input_file": "Miami TAC.tif",
        "window": (6429, 7006, 3917, 1732),
        "masks": [ [(6429,7006), (7384,7006), (7384,7392), (6429,7392)] ],
        "gcps": [(7687, 7329, -81.5, 25), (8879, 7323, -81, 25), (10073, 7311, -80.5, 25), (6494, 8640, -82, 24.5), (7692, 8638, -81.5, 24.5), (8890, 8631, -81, 24.5), (10089, 8619, -80.5, 24.5)],
    },
    "Minneapolis-St Paul TAC": {
        "window": (1801, 556, 5891, 5384),
    },
    "Montreal SEC": {
        "window": (2101, 60, 15762, 11355),
        "geobound": (-77, 44, None, None),
    },
    "NARC": {
        "window": (82, 82, 13525, 9558),
        "masks": [ [(82,82), (5308,82), (5308,2065), (4561,2065), (4561,2257), (3815,2257), (3815,3026), (3068,3026), (3068,3411), (2322,3411), (2322,3680), (820,3680), (820,3487), (82,3787)], [(82,8695), (1283,8695), (1283,9640), (82,9640)] ],
    },
    "New Orleans FLY": {
        "window": (1546, 574, 7414, 5389),
    },
    "New Orleans SEC": {
        "window": (1721, 0, 14896, 11289),
        "geobound": (-91, 28, None, None),
    },
    "New Orleans TAC": {
        "window": (1551, 518, 7414, 5389),
    },
    "New York HEL": {
        "window": (2989, 1074, 10407, 7769),
        "masks": [ [(2989,1074), (8387,1074), (8387,3333), (2989,3315)], [(11824,1074), (13396,1074), (13396,3309), (11836,3318)], [(2989,5936), (4445,5945), (4445,8843), (2989,8843)] ],
    },
    "New York SEC": {
        "window": (1605, 0, 16257, 11363),
        "geobound": (-77, 40, None, None),
    },
    "New York TAC": {
        "window": (3061, 568, 8906, 5457),
    },
    "Nome SEC": {
        "window": (883, 0, 15717, 11442),
        "masks": [ [(2143,104), (4241,105), (4240,3013), (2142,3012)] ],
        "geobound": (-171.5, 64, None, None),
    },
    "Nome SEC Lavrentiya-Provideniya Inset": {
        "input_file": "Nome SEC.tif",
        "window": (2302, 261, 1785, 2580),
        "gcps": [(2708, 313, -173, 66), (3245, 321, -172, 66), (3780, 320, -171, 66), (2678, 1629, -173, 65), (3235, 1637, -172, 65), (3793, 1637, -171, 65), (3228, 2735, -172, 64.166)],
    },
    "Omaha SEC": {
        "window": (1634, 52, 16287, 12090),
        "geobound": (-101, 40, None, None),
    },
    "Orlando FLY": {
        "window": (137, 370, 8751, 7171),
    },
    "Orlando TAC": {
        "window": (3009, 443, 8750, 7170),
    },
    "Philadelphia TAC": {
        "window": (1735, 639, 5905, 5854),
    },
    "Phoenix FLY": {
        "window": (3064, 119, 7407, 6811),
    },
    "Phoenix SEC": {
        "window": (1987, 662, 15898, 11589),
        "geobound": (-116, None, None, None),
    },
    "Phoenix TAC": {
        "window": (3055, 585, 7407, 6810),
    },
    "Pittsburgh TAC": {
        "window": (1620, 548, 5897, 5386),
    },
    "Point Barrow SEC": {
        "window": (709, 22, 15846, 11533),
        "geobound": (-157, 68, None, None),
    },
    "PORC_COMP East": {
        "input_file": "PORC_COMP.tif",
        "window": (62, 63, 17693, 12293),
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "PORC_COMP West": {
        "input_file": "PORC_COMP.tif",
        "window": (62, 63, 17693, 12293),
        "masks": [ [(9907,11846), (13344,11846), (13344,10336), (14976,10336), (14976,9665), (16373,9665), (16373, 9931), (17755,9931), (17755,12356), (9907,12356)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "PORC_NE East": {
        "input_file": "PORC_NE.tif",
        "window": (62, 63, 17693, 12293),
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "PORC_NE West": {
        "input_file": "PORC_NE.tif",
        "window": (62, 63, 17693, 12293),
        "masks": [ [(13453,63), (17755,63), (17755,4140), (16636,4140), (16366,1998), (14734,1998), (14734,488), (13453,488),] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "PORC_NW East": {
        "input_file": "PORC_NW.tif",
        "window": (62, 63, 17693, 12293),
        "masks": [ [(62,63), (5325,63), (5325,530), (3085,530), (3085,2040), (2843,2040), (2843,3930), (1446,3930), (1446,2445), (62,2445)] ],
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "PORC_NW West": {
        "input_file": "PORC_NW.tif",
        "window": (62, 63, 17693, 12293),
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "PORC_SE East": {
        "input_file": "PORC_SE.tif",
        "window": (62, 63, 17693, 12293),
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "PORC_SE West": {
        "input_file": "PORC_SE.tif",
        "window": (62, 63, 17693, 12293),
        "masks": [ [(62,6097), (614,6097), (614,9334), (2124,9334), (2124,10966), (3354,10966), (3354,12356), (62,12356)] ],
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "PORC_SW East": {
        "input_file": "PORC_SW.tif",
        "window": (62, 63, 17693, 12293),
        "masks": [ [(14388,10966), (15778,10966), (15778,9334), (17288,9334), (17288,6099), (17755,6099), (17755,12356), (14388,12356)] ],
        "geobound": (None, None, 180, None),
        "antimeridian": True,
    },
    "PORC_SW West": {
        "input_file": "PORC_SW.tif",
        "window": (62, 63, 17693, 12293),
        "geobound": (-180, None, None, None),
        "antimeridian": True,
    },
    "Portland TAC": {
        "window": (1845, 3094, 4118, 4288),
    },
    "Puerto Rico-VI TAC": {
        "window": (1489, 68, 16281, 5974),
    },
    "Salt Lake City FLY": {
        "window": (1595, 159, 7408, 6754),
    },
    "Salt Lake City SEC": {
        "window": (1603, 15, 16294, 12085),
        "geobound": (-117, 40, None, None),
    },
    "Salt Lake City TAC": {
        "window": (1593, 699, 7408, 6755),
    },
    "Samoan Islands Inset SEC": {
        "window": (33, 53, 8928, 3877),
        "masks": [ [(6996,53), (8961,53), (8961,2542), (6996,2542)] ],
    },
    "San Antonio SEC": {
        "window": (1738, 0, 15117, 11290),
        "geobound": (-103, 28, -96.5, None),
    },
    "San Diego FLY": {
        "window": (307, 218, 7411, 5815),
    },
    "San Diego TAC": {
        "window": (4626, 178, 7411, 5816),
    },
    "San Francisco FLY": {
        "window": (3218, 693, 7377, 6158),
    },
    "San Francisco SEC": {
        "window": (1449, 1001, 15160, 11197),
        "geobound": (-125, 36, None, None),
    },
    "San Francisco TAC": {
        "window": (3069, 362, 7377, 6157),
    },
    "Seattle FLY": {
        "window": (1625, 272, 5915, 6920),
    },
    "Seattle SEC": {
        "window": (2155, 0, 15703, 12104),
        "geobound": (-125, None, None, None),
    },
    "Seattle TAC": {
        "window": (1674, 547, 5916, 6919),
    },
    "Seward SEC": {
        "window": (1568, 11, 16290, 6181),
        "masks": [ [(3152,6104), (17858,6089), (17858,6192), (3152,6192)] ],
        "geobound": (-152.5, None, None, None),
    },
    "St Louis FLY": {
        "window": (1614, 556, 5900, 5394),
    },
    "St Louis SEC": {
        "window": (2918, 41, 15017, 11336),
        "geobound": (-91, 36, None, None),
    },
    "St Louis TAC": {
        "window": (1561, 560, 5901, 5394),
    },
    "Tampa FLY": {
        "window": (100, 483, 5919, 6902),
    },
    "Tampa TAC": {
        "window": (1531, 484, 5921, 6902),
    },
    "Twin Cities SEC": {
        "window": (2201, 45, 15705, 12087),
        "geobound": (-101, None, None, None),
    },
    "Twin Cities SEC Lake Of The Woods Inset": {
        "input_file": "Twin Cities SEC.tif",
        "window": (233, 9389, 1836, 2272),
        "gcps": [(291, 9476, -95.5, 49.5), (1149, 9479, -95, 49.5), (2008, 9476, -94.5, 49.5), (282, 10792, -95.5, 49), (1149, 10796, -95, 49), (2016, 10793, -94.5, 49)],
    },
    "U.S. Gulf Coast HEL": {
        "window": (1534, 0, 15017, 6601),
        "geobound": (None, 26, None, None),
    },
    "U.S. VFR Wall Planning Chart": {
        "window": (466, 297, 17636, 10698),
        "masks": [ [(593,7940), (2099,7932), (2116,10939), (601,10947)] ],
    },
    "US_IFR_PLAN_EAST": {
        "window": (2, 260, 9794, 11454),
        "masks": [ [(2,11714), (5675,11714), (5675,12292), (2,12292)], [(8326,4794), (9796,3438), (9796,12292), (8326,12292)] ],
    },
    "US_IFR_PLAN_WEST": {
        "window": (3854, 260, 9794, 12032),
        "masks": [ [(3854,7477), (12760,11931), (12400,12292), (3854,12292)] ],
    },
    "Washington HEL": {
        "window": (3029, 314, 10391, 5579),
    },
    "Washington Inset HEL": {
        "window": (50, 142, 2872, 3596),
    },
    "Washington SEC": {
        "window": (1512, 25, 15150, 11302),
        "masks": [ [(14042,6502), (16549,6490), (16563,9079), (14053,9091)] ],
        "geobound": (-79, 36, None, None),
    },
    "Washington SEC Norfolk Inset": {
        "input_file": "Washington SEC.tif",
        "window": (14118, 6565, 2426, 2388),
        "gcps": [(14183, 6713, -76.5, 37.166), (15230, 6712, -76.25, 37.166), (16278, 6708, -76, 37.166), (14182, 7586, -76.5, 37), (15232, 7585, -76.25, 37), (16283, 7582, -76, 37), (14181, 8895, -76.5, 36.75), (15235, 8894, -76.25, 36.75), (16289, 8891, -76, 36.75)],
    },
    "WATRS": {
        "window": (72, 1788, 7167, 8426),
        "masks": [ [(72,1788), (2803,1788), (2803,2027), (2243,2027), (2243,2316), (562,2316), (562,3229), (72,3229)] ],
    },
    "Western Aleutian Islands East SEC East": {
        "input_file": "Western Aleutian Islands East SEC.tif",
        "window": (968, 211, 15573, 5902),
        "geobound": (178, 51, 180, None),
        "antimeridian": True,
    },
    "Western Aleutian Islands East SEC West": {
        "input_file": "Western Aleutian Islands East SEC.tif",
        "window": (968, 211, 15573, 5902),
        "geobound": (-180, 51, None, None),
        "antimeridian": True,
    },
    "Western Aleutian Islands West SEC": {
        "window": (1557, 145, 14987, 5888),
        "geobound": (None, 51, None, None),
    },
    "Wichita SEC": {
        "window": (1437, 60, 15204, 11311),
        "geobound": (-104, 36, None, None),
    },
}

merge_groups = {
    "VFR Sectional": [
        "Albuquerque SEC",
        "Atlanta SEC",
        "Billings SEC",
        "Brownsville SEC",
        "Charlotte SEC",
        "Cheyenne SEC",
        "Chicago SEC",
        "Cincinnati SEC",
        "Dallas-Ft Worth SEC",
        "Denver SEC",
        "Detroit SEC",
        "El Paso SEC",
        "Great Falls SEC",
        "Green Bay SEC",
        "Halifax SEC",
        "Hawaiian Islands SEC",
        "Houston SEC",
        "Jacksonville SEC",
        "Kansas City SEC",
        "Klamath Falls SEC",
        "Lake Huron SEC",
        "Las Vegas SEC",
        "Los Angeles SEC",
        "Memphis SEC",
        "Miami SEC",
        "Montreal SEC",
        "New Orleans SEC",
        "New York SEC",
        "Omaha SEC",
        "Phoenix SEC",
        "Salt Lake City SEC",
        "San Antonio SEC",
        "San Francisco SEC",
        "Seattle SEC",
        "St Louis SEC",
        "Twin Cities SEC",
        "Washington SEC",
        "Wichita SEC",
    ],
    "VFR Sectional - Alaska": [
        "Western Aleutian Islands East SEC",
        "Western Aleutian Islands West SEC",
        "Dutch Harbor SEC",
        "Cold Bay SEC",
        "Ketchikan SEC",
        "Kodiak SEC",
        "Seward SEC",
        "Juneau SEC",
        "Bethel SEC",
        "McGrath SEC",
        "Anchorage SEC",
        "Nome SEC",
        "Fairbanks SEC",
        "Dawson SEC",
        "Cape Lisburne SEC",
        "Point Barrow SEC",
    ],
    "VFR Sectional - Pacific": [
        "Honolulu Inset SEC",
        "Mariana Islands Inset SEC",
        "Samoan Islands Inset SEC",
    ],
    "VFR Terminal Area Chart": [
        "Anchorage TAC",
        "Atlanta TAC",
        "Baltimore Washington TAC",
        "Boston TAC",
        "Charlotte TAC",
        "Chicago TAC",
        "Cincinnati TAC",
        "Cleveland TAC",
        "Colorado Springs TAC",
        "Dallas-Ft Worth TAC",
        "Denver TAC",
        "Detroit TAC",
        "Fairbanks TAC",
        "Houston TAC",
        "Kansas City TAC",
        "Las Vegas TAC",
        "Los Angeles TAC",
        "Memphis TAC",
        "Miami TAC",
        "Minneapolis-St Paul TAC",
        "New Orleans TAC",
        "New York TAC",
        "Orlando TAC",
        "Philadelphia TAC",
        "Phoenix TAC",
        "Portland TAC",
        "Pittsburgh TAC",
        "Puerto Rico-VI TAC",
        "Salt Lake City TAC",
        "San Diego TAC",
        "San Francisco TAC",
        "Seattle TAC",
        "St Louis TAC",
        "Tampa TAC",
    ],
    "VFR Wall Planning Chart": [
        "US VFR Wall Planning Chart",
    ],
    "VFR Wall Planning Chart - Alaska": [
        "Alaska Wall Planning Chart",
    ],
    "Worldwide Route System": [
        "GOM_CN",
        "GOM_CS",
        "GOM_WN",
        "GOM_WS",
        "NARC",
        "PORC_NE",
        "PORC_NW",
        "PORC_SE",
        "PORC_SW",
        "WATRS",
    ],
}
'''
    local -r insets_chart_array=( Dutch_Harbor_Inset Jacksonville_Inset Juneau_Inset Ketchikan_Inset Kodiak_Inset Norfolk_Inset Pribilof_Islands_Inset )
    check_group "${insets_chart_array[@]}"

    local -r heli_chart_array_1000000=( U_S_Gulf_Coast_HEL )
    check_group "${heli_chart_array_1000000[@]}"

    local -r heli_chart_array_250000=( Eastern_Long_Island_HEL )
    check_group "${heli_chart_array_250000[@]}"

    local -r heli_chart_array_125000=( Baltimore_HEL Boston_HEL Chicago_HEL Dallas_Ft_Worth_HEL Detroit_HEL Houston_North_HEL Houston_South_HEL Los_Angeles_East_HEL Los_Angeles_West_HEL New_York_HEL Washington_HEL )
    check_group "${heli_chart_array_125000[@]}"

    local -r heli_chart_array_90000=( Chicago_O_Hare_Inset_HEL Dallas_Love_Inset_HEL )
    check_group "${heli_chart_array_90000[@]}"

    local -r heli_chart_array_62500=( Washington_Inset_HEL )
    check_group "${heli_chart_array_62500[@]}"

    local -r heli_chart_array_50000=( Boston_Downtown_HEL Downtown_Manhattan_HEL )
    check_group "${heli_chart_array_50000[@]}"

    local -r grand_canyon_chart_array=( Grand_Canyon_General_Aviation Grand_Canyon_Air_Tour_Operators )
    check_group "${grand_canyon_chart_array[@]}"

    local -r enroute_chart_array_2000000=( ENR_CL01 ENR_CL02 ENR_CL03 ENR_CL05 ENR_AKH01 ENR_AKH02 )
    check_group "${enroute_chart_array_2000000[@]}"

    local -r enroute_chart_array_1000000=( ENR_AKL01 ENR_AKL02C ENR_AKL02E ENR_AKL02W ENR_AKL03 ENR_AKL04 ENR_AKL01_JNU ENR_L09 ENR_L11 ENR_L12 ENR_L13 ENR_L14 ENR_L21 ENR_L32 ENR_CL06 Mexico_City_Area Miami_Nassau Lima_Area Guatemala_City_Area
                                          Dominican_Republic_Puerto_Rico_Area Bogota_area ENR_P02 ENR_AKH01_SEA ENR_H01 ENR_H02 ENR_H03 ENR_H04 ENR_H05  ENR_H06 ENR_H07 ENR_H08 ENR_H09 ENR_H10 ENR_H11 ENR_H12  )
    check_group "${enroute_chart_array_1000000[@]}"

    local -r enroute_chart_array_500000=( Buenos_Aires_Area Santiago_Area Rio_De_Janeiro_Area Panama_Area ENR_AKL01_VR ENR_AKL04_ANC ENR_AKL03_FAI ENR_AKL03_OME ENR_L01 ENR_L02 ENR_L03 ENR_L04 ENR_L05 ENR_L06N ENR_L06S ENR_L07 ENR_L08 ENR_L10
                                         ENR_L15 ENR_L16 ENR_L17 ENR_L18 ENR_L19 ENR_L20 ENR_L22 ENR_L23 ENR_L24 ENR_L25 ENR_L26 ENR_L27 ENR_L28 ENR_L29 ENR_L30 ENR_L31 ENR_L33 ENR_L34 ENR_L35 ENR_L36 ENR_A01_DCA ENR_A02_DEN ENR_A02_PHX )
    check_group "${enroute_chart_array_500000[@]}"

    local -r enroute_chart_array_250000=( ENR_A01_ATL ENR_A01_JAX ENR_A01_MIA ENR_A01_MSP ENR_A01_STL ENR_A02_DFW ENR_A02_ORD ENR_A02_SFO ENR_A01_DET ENR_A02_LAX ENR_A02_MKC )
    check_group "${enroute_chart_array_250000[@]}"

    local -r caribbean_chart_array=( Caribbean_1_VFR_Chart Caribbean_2_VFR_Chart )
    check_group "${caribbean_chart_array[@]}"

    local -r planning_chart_array=( US_IFR_PLAN_EAST US_IFR_PLAN_WEST )
    check_group "${planning_chart_array[@]}"
'''