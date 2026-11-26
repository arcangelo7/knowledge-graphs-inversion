DROP TABLE IF EXISTS "PredicateMaps";
CREATE TABLE "PredicateMaps" (
    "ID" INTEGER,
    "Name" VARCHAR(50),
    "FirstProperty" VARCHAR(50),
    "SecondProperty" VARCHAR(50)
);
INSERT INTO "PredicateMaps" VALUES (1, 'Alice', 'label', 'name');
