# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from conformance.outcome import CaseOutcome, InversionOutcome
from kgi.comparison import PartialLoss

TestKey = tuple[str, str]

EXPECTATIONS: dict[TestKey, CaseOutcome] = {
    ("r2rml", "R2RMLTC0000"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0001a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0001b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0002a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0002b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0002c"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0002d"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0002e"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0002f"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0002g"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0002h"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0002i"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0002j"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0003b"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0003c"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0004a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0004b"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0005a"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("r2rml", "R2RMLTC0005b"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("r2rml", "R2RMLTC0006a"): CaseOutcome(InversionOutcome.NON_INVERTIBLE),
    ("r2rml", "R2RMLTC0007a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007c"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007d"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007e"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007f"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007g"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0007h"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0008a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0008b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0008c"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0009a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0009b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0009c"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0009d"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0010a"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0010b"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0010c"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0011a"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0011b"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0012a"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST, PartialLoss.TABLES_LOST}),
    ),
    ("r2rml", "R2RMLTC0012b"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST, PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("r2rml", "R2RMLTC0012c"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0012d"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0012e"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("r2rml", "R2RMLTC0013a"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.ROWS_LOST}),
    ),
    ("r2rml", "R2RMLTC0014a"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0014b"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0014c"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0014d"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0015a"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0015b"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0016a"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0016b"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0016c"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0016d"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0016e"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "R2RMLTC0018a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "R2RMLTC0019a"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("r2rml", "R2RMLTC0019b"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "R2RMLTC0020a"): CaseOutcome(InversionOutcome.NON_INVERTIBLE),
    ("r2rml", "R2RMLTC0020b"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("r2rml", "INVTC0001"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0002"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0003"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0004"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0005"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0006"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0007"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("r2rml", "INVTC0008"): CaseOutcome(InversionOutcome.NON_INVERTIBLE),
    ("rml", "RMLTC0000-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0001a-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0001b-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0002a-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0002b-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0002c-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0002d-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0002e-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0002f-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0002g-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0002h-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0002i-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0002j-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0003a-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0003b-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0003c-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0004a-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0004b-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0005a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("rml", "RMLTC0005b-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("rml", "RMLTC0006a-RDB"): CaseOutcome(InversionOutcome.NON_INVERTIBLE),
    ("rml", "RMLTC0007a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007b-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007c-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007d-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007e-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007f-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007g-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0007h-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0008a-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0008b-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0008c-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0009a-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0009b-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0009c-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0009d-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0010a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0010b-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0010c-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0011a-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0011b-RDB"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("rml", "RMLTC0012a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST, PartialLoss.TABLES_LOST}),
    ),
    ("rml", "RMLTC0012b-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST, PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("rml", "RMLTC0012c-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0012d-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0012e-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.MULTIPLICITY_LOST}),
    ),
    ("rml", "RMLTC0013a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.ROWS_LOST}),
    ),
    ("rml", "RMLTC0014d-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0015a-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0015b-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0016a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0016b-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0016c-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0016d-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0016e-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
    ("rml", "RMLTC0019a-RDB"): CaseOutcome(InversionOutcome.NOT_SUPPORTED),
    ("rml", "RMLTC0019b-RDB"): CaseOutcome(InversionOutcome.ERROR_TEST_CASE),
    ("rml", "RMLTC0020a-RDB"): CaseOutcome(InversionOutcome.NON_INVERTIBLE),
    ("rml", "RMLTC0021a-RDB"): CaseOutcome(
        InversionOutcome.PARTIALLY_INVERTED,
        frozenset({PartialLoss.COLUMNS_LOST}),
    ),
}

SOUFFLE_PROVENANCE_EXPECTATIONS: dict[TestKey, CaseOutcome] = {
    ("r2rml", "R2RMLTC0020a"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0001"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0002"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0003"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0004"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0005"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0006"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
    ("r2rml", "INVTC0007"): CaseOutcome(InversionOutcome.FULLY_INVERTED),
}


def expected_outcome(
    suite_id: str, test_id: str, souffle_provenance: bool = False
) -> CaseOutcome | None:
    key = (suite_id, test_id)
    if souffle_provenance and key in SOUFFLE_PROVENANCE_EXPECTATIONS:
        return SOUFFLE_PROVENANCE_EXPECTATIONS[key]
    return EXPECTATIONS.get(key)
