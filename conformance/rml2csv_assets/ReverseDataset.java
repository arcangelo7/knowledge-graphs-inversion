// SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
// SPDX-License-Identifier: ISC

import gr.hcmr.imbbc.rmlreverse.project.strategy.ReverseRML2CSV;

public class ReverseDataset {
    public static void main(String[] args) {
        new ReverseRML2CSV(args[0], args[1], args[2], args[3]).reverseRML();
    }
}
