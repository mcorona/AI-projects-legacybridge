-- SYNTHETIC stand-in for the `sicpt` inventory system, in the style of a 1990s ERP.
-- It is NOT the real sicpt: replace it when the real DDL is available (docs/legacy-defects.md).
-- The defects below are INTENTIONAL (catalogue: SIC-01..SIC-18). Do not fix them. Like the
-- invoicing schema, there are no primary keys, no foreign keys and no indexes.

CREATE TABLE invart (                 -- article master
    clave    CHAR(12)    NOT NULL,    -- SIC-01 'AR00007' here, 'P0000007' in productos (no shared key)
    descr    VARCHAR(60),             -- SIC-02 UPPERCASE, some with mojibake ('Ã±' for 'ñ')
    unimed   CHAR(3),                 -- SIC-03 unit code spelled several ways ('PZA', 'PZ', 'pza')
    factor   INTEGER,                 -- SIC-04 units per box ('CJA'); 0 or NULL on some rows
    costo    VARCHAR(12),             -- SIC-05 unit cost as text, some with a decimal comma
    activo   CHAR(1),                 -- SIC-06 'S'/'N'/NULL, NULL means inactive
    alta     CHAR(8)                  -- SIC-07 registration date YYYYMMDD, some as DDMMYYYY
);

CREATE TABLE invalm (                 -- warehouses
    clave    CHAR(3)     NOT NULL,
    nombre   VARCHAR(30),
    tipo     CHAR(1)                  -- SIC-08 P=own stock, T=in transit, C=on consignment
);

CREATE TABLE invexi (                 -- stock snapshots, one per month-end cut
    alm      CHAR(3),
    art      CHAR(12),
    cant     NUMERIC(14,3),           -- SIC-09 negative quantities exist (data errors)
    corte    CHAR(6)                  -- SIC-10 YYYYMM: old cuts were never deleted
);

CREATE TABLE invprv (                 -- suppliers
    clave    CHAR(6)     NOT NULL,
    nombre   VARCHAR(60),
    rfc      VARCHAR(13),             -- SIC-16 the same supplier appears under two keys
    activo   CHAR(1)
);

CREATE TABLE invmov (                 -- inventory movements
    fol      INTEGER,
    fecha    CHAR(8),
    alm      CHAR(3),
    art      CHAR(12),                -- SIC-15 may point to an article that does not exist
    tipo     CHAR(1),                 -- SIC-11 E/S/T/A, but 2000 used I (in) and O (out)
    cant     NUMERIC(14,3),           -- SIC-12 issues are negative up to 2001, positive after
    uni      CHAR(3),                 -- SIC-18 quantity is in boxes ('CJA') or base units ('PZA')
    prov     CHAR(6),
    ref      VARCHAR(20),             -- SIC-13 reused: purchase order, invoice folio or free text
    cancel   CHAR(1),                 -- SIC-14 'C' = cancelled; a reversal row 'CANC-<fol>' exists
    obs      VARCHAR(100)             -- SIC-17 free text, may contain instructions
);
