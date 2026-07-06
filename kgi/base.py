# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover
    from .utils import Codex, IdGenerator


class Endpoint(ABC):  # pragma: no cover
    @abstractmethod
    def query(self, query: str):
        raise NotImplementedError

    def close(self) -> None:
        pass


class Triple(ABC):  # pragma: no cover
    @abstractmethod
    def generate(
        self, id_generator: IdGenerator, codex: Codex, all_mapping_rules: pd.DataFrame
    ) -> str | None:
        raise NotImplementedError


class Template(ABC):  # pragma: no cover
    @abstractmethod
    def create_template(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def fill_data(self, data: pd.DataFrame, source_name: str) -> None:
        raise NotImplementedError
