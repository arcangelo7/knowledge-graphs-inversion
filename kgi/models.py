# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass

import pandas as pd


@dataclass
class ReconstructedTable:
    name: str
    data: pd.DataFrame
