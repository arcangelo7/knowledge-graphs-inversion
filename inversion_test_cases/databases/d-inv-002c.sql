DROP TABLE IF EXISTS "ObjectMaps";
CREATE TABLE "ObjectMaps" (
    "ID" INTEGER,
    "Name" VARCHAR(50),
    "FirstFriend" VARCHAR(50),
    "SecondFriend" VARCHAR(50)
);
INSERT INTO "ObjectMaps" VALUES (1, 'Alice', 'bob', 'carol');
