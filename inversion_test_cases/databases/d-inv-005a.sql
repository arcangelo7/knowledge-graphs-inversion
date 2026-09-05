DROP TABLE IF EXISTS child;
DROP TABLE IF EXISTS parent;
CREATE TABLE child (
    id INTEGER,
    join_key VARCHAR(10)
);
CREATE TABLE parent (
    id INTEGER,
    join_key VARCHAR(10)
);
INSERT INTO child VALUES (1, 'a');
INSERT INTO child VALUES (2, 'b');
INSERT INTO parent VALUES (10, 'a');
INSERT INTO parent VALUES (20, 'b');
