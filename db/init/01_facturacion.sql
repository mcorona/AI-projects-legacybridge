-- SYNTHETIC invoicing schema in the style of a year-2000 ERP.
-- The defects below are INTENTIONAL (catalogue: docs/legacy-defects.md, FAC-01..FAC-10). Do not fix
-- them: they are the test case. Like the original system, there are no primary keys, no foreign
-- keys and no indexes.

CREATE TABLE clientes (               -- customer master
    clave      CHAR(10)    NOT NULL,  -- zero-padded number, e.g. '0000000042'
    nombre     VARCHAR(60),
    rfc        VARCHAR(13),           -- current tax id
    estado     CHAR(3),               -- state code: JAL, NLE, CDMX, ...
    telefono   VARCHAR(15),
    email      VARCHAR(60),
    status     CHAR(1),               -- FAC-03 A=active, I=inactive, B=logically deleted
    falta      CHAR(8)                -- FAC-05 registration date as text YYYYMMDD
);

CREATE TABLE productos (              -- product master
    clave        CHAR(8)   NOT NULL,  -- e.g. 'P0000123'
    descripcion  VARCHAR(80),         -- FAC-10 free text, may contain instructions
    linea        CHAR(2),             -- product line code
    precio       NUMERIC(12,2),       -- list price
    status       CHAR(1)              -- FAC-03 A=active, B=logically deleted
);

CREATE TABLE faccab (                 -- invoice header
    serie      CHAR(2),               -- 'A ' or 'B ' (padded)
    numfac     CHAR(10)    NOT NULL,  -- zero-padded folio. FAC-09: re-captured folios repeat
    cliente    CHAR(10),              -- FAC-02 no foreign key: may point to nobody
    rfc        VARCHAR(13),           -- FAC-06 tax id copied at invoice time, may differ from clientes
    fecha      CHAR(8),               -- FAC-05 invoice date as text YYYYMMDD, sometimes garbage
    subtotal   CHAR(15),              -- FAC-04 amounts stored as right-aligned text
    iva        CHAR(15),              -- FAC-04
    total      CHAR(15),              -- FAC-04 / FAC-08 currency has no exchange rate
    ref1       VARCHAR(12),           -- FAC-07 reused column: meaning depends on the year
    status     CHAR(1),               -- FAC-03 V=valid, C=cancelled, B=logically deleted
    fcaptura   CHAR(14)               -- FAC-09 capture timestamp YYYYMMDDHHMMSS
);

CREATE TABLE facdet (                 -- invoice lines
    serie      CHAR(2),
    numfac     CHAR(10),              -- FAC-01 sometimes left-padded with spaces instead of zeros
    renglon    SMALLINT,
    producto   CHAR(8),               -- FAC-02 no foreign key
    cantidad   NUMERIC(12,3),
    precio     VARCHAR(12),           -- FAC-04 unit price as text
    importe    VARCHAR(15),           -- FAC-04 line amount as text
    obs        VARCHAR(100),          -- FAC-10 free text, may contain instructions
    status     CHAR(1)                -- FAC-03 A=active, B=logically deleted
);
