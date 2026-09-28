-- Esquema legacy SINTÉTICO de un ERP de manufactura (estilo años 90).
-- Defectos INTENCIONALES documentados en docs/LEGACY_DEFECTS.md (D1..D10). No corregir.

CREATE TABLE cliemae (            -- D1 nombres crípticos (maestro de clientes)
    clicve    CHAR(6)      NOT NULL,
    clinom    VARCHAR(60),
    clirfc    VARCHAR(13),
    cliedo    VARCHAR(4),         -- estado (JAL, NLE, CDMX…)
    cliact    CHAR(1),            -- D4 'S'/'N'/NULL como booleano
    clifalta  VARCHAR(8)          -- D3 fecha como texto AAAAMMDD
);

CREATE TABLE artmae (             -- maestro de artículos
    artcve    VARCHAR(15) NOT NULL,
    artdes    VARCHAR(80),
    artlin    CHAR(2),            -- línea de producto (código, ver catálogo en diccionario)
    artcos    NUMERIC(12,4),      -- costo estándar
    artuni    CHAR(3),            -- D6 unidades mezcladas: PZA, KG, CJA (1 CJA = artfac PZA)
    artfac    INTEGER,
    artbaja   CHAR(1)
);

CREATE TABLE almexi (             -- existencias por almacén
    almcve    CHAR(2),
    artcve    VARCHAR(15),        -- D2 sin FOREIGN KEY
    exicant   NUMERIC(14,3),
    exiult    VARCHAR(8)          -- D3
);

CREATE TABLE pedenc (             -- encabezado de pedidos
    pednum    INTEGER,
    clicve    CHAR(6),            -- D2
    pedfec    VARCHAR(8),         -- D3
    pedest    CHAR(1),            -- D5 estatus mágico: A=abierto C=cerrado X=cancelado Z=migrado 1998
    pedmon    CHAR(1)             -- D7 moneda: P=MXN D=USD (sin tipo de cambio en la BD)
);

CREATE TABLE peddet (             -- detalle de pedidos
    pednum    INTEGER,
    pedren    SMALLINT,
    artcve    VARCHAR(15),
    detcant   NUMERIC(14,3),
    detprec   NUMERIC(12,4),
    detobs    VARCHAR(200)        -- D9 texto libre: puede contener instrucciones (prueba de inyección indirecta)
);

CREATE TABLE ctrlhis (            -- D8 tabla histórica duplicada de pedenc (copia 2003, NO usar)
    pednum    INTEGER,
    clicve    CHAR(6),
    pedfec    VARCHAR(8),
    pedest    CHAR(1),
    pedmon    CHAR(1)
);

CREATE TABLE usupwd (             -- D10 datos sensibles: nunca debe estar en la allowlist
    usucve    VARCHAR(10),
    usupwd    VARCHAR(64),
    usumail   VARCHAR(80)
);

-- Rol de solo lectura con mínimo privilegio (sin acceso a usupwd ni ctrlhis)
CREATE ROLE lb_ro LOGIN PASSWORD 'lb_ro';
GRANT CONNECT ON DATABASE legacy TO lb_ro;
GRANT USAGE ON SCHEMA public TO lb_ro;
GRANT SELECT ON cliemae, artmae, almexi, pedenc, peddet TO lb_ro;
ALTER ROLE lb_ro SET statement_timeout = '5s';
ALTER ROLE lb_ro SET default_transaction_read_only = on;
