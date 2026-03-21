# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC


class KGIError(Exception):
    pass


class MappingError(KGIError):
    pass


class UnsupportedMappingError(KGIError):
    pass


class NonInvertibleError(KGIError):
    pass


class NoDataError(KGIError):
    pass
