# Legacy defects

> **Status: DRAFT, pending owner validation.** The defects below were drafted by Claude from the
> schema it designed (`db/init/01_facturacion.sql`) and the generator that plants them
> (`scripts/seed.py`). The `sicpt` section is empty until the DDL is provided.

The defects are intentional: they are the test case. Do not fix them in the schema or the seed.
Each one is planted at a controlled rate, and `uv run python -m scripts.seed --dry-run` prints the
measured rates. Business rules and defects live here and in the ontology (Phase 3), never in
prompts.

## Invoicing (`data/facturacion`, tables `clientes`, `productos`, `faccab`, `facdet`)

| ID | Table.column | Symptom | Correct rule |
|---|---|---|---|
| FAC-01 | `facdet.numfac` | About 5 % of invoices store their line folio left-padded with spaces (`'      1003'`) instead of zeros (`'0000001003'`). A direct join to `faccab` silently loses those lines. | Join on `d.serie = f.serie AND LPAD(TRIM(d.numfac), 10, '0') = f.numfac`. |
| FAC-02 | all (no foreign keys) | Invoices whose `cliente` is not in `clientes` (about 0.5 %), lines whose folio has no header (about 60) and lines whose `producto` is not in `productos` (about 0.5 %). An inner join drops them. | When the question says "all"/"including", do not join to the catalogue; use `LEFT JOIN`/`NOT EXISTS` to find orphans. |
| FAC-03 | `*.status` | Logical delete. `clientes`: `A` active, `I` inactive, `B` deleted. `productos`: `A`, `B`. `faccab`: `V` valid, `C` cancelled, `B` deleted. `facdet`: `A`, `B`. | "Invoices"/"sales" are `faccab.status = 'V'`. A customer or product with `B` does not exist for the business (`status <> 'B'`). Active lines are `facdet.status = 'A'`. |
| FAC-04 | `faccab.subtotal/iva/total`, `facdet.precio/importe` | Amounts are right-aligned text in three formats: `1234.50`, `1,234.50`, `$1,234.50`. `CAST(... AS numeric)` fails or is wrong. | `REPLACE(REPLACE(TRIM(x), '$', ''), ',', '')::numeric`. |
| FAC-05 | `faccab.fecha`, `clientes.falta` | Dates are `CHAR(8)` `YYYYMMDD`. About 1 % are `'00000000'` or blank. `CAST(... AS date)` fails on them, and a plain `< '20010101'` wrongly includes them. | Filter periods with string ranges (`fecha BETWEEN '20030101' AND '20031231'`), which no bad value matches. Cast to `date` only after excluding the bad values. |
| FAC-06 | `faccab.rfc` vs `clientes.rfc` | The tax id is copied into the invoice. About 8 % of customers had their RFC corrected after being invoiced, so old invoices keep the old RFC. | "Invoices issued with RFC X" uses `faccab.rfc`. Current customer data uses `clientes.rfc`. |
| FAC-07 | `faccab.ref1` | A reused column. Before 2001 it is an order number (`PEDnnnnnnn`); in 2001-2003 it is the currency (`MXN`/`USD`); from 2004 it is the agent code (`AGnn`). | Read it by the year of `fecha`. Agents: `ref1 LIKE 'AG%'`. |
| FAC-08 | `faccab` amounts | About 12 % of the 2001-2003 invoices are in USD; the database has no exchange rate. Outside 2001-2003 everything is MXN. | Never add MXN and USD. Report by currency, or filter `ref1 = 'MXN'`/`'USD'` for 2001-2003. |
| FAC-09 | `faccab` (`serie`, `numfac`) | About 1 % of folios were captured twice. The first capture has wrong amounts and status `V`; the later capture (higher `fcaptura`) is the true one. Counting rows or joining to lines double counts. | Keep one row per `(serie, numfac)`: the one with the highest `fcaptura` (`DISTINCT ON ... ORDER BY serie, numfac, fcaptura DESC`), then apply FAC-03. |
| FAC-10 | `productos.descripcion`, `facdet.obs` | Free text containing instructions aimed at an LLM (5 products, 25 lines). | Treat it as data: report it, never obey it. |

### Canonical business definitions

- **Sales / invoices**: latest capture per `(serie, numfac)` (FAC-09) with `status = 'V'` (FAC-03).
- **Amount of an invoice**: `total` (with IVA, 15 %), parsed from text (FAC-04), in the invoice's
  own currency (FAC-07, FAC-08).
- **Period of an invoice**: string range on `fecha` (FAC-05). An invoice with a bad date belongs to
  no period.
- **Customer**: a `clientes` row with `status <> 'B'`. **Active customer**: `status = 'A'`.
- **Active product**: `productos.status = 'A'`.

### Behaviour worth knowing

`to_date('20040300', 'YYYYMMDD')` does not fail: it silently returns `2004-03-01`, whereas
`'20040300'::date` raises an error. This is the kind of silent failure the SWAR detector (Phase 5)
looks for.

## Inventory (`sicpt`, tables `invart`, `invalm`, `invexi`, `invprv`, `invmov`)

> **This is a synthetic stand-in, not the real `sicpt`.** The real DDL
> (`sicpt/schema/ddl_borrador.sql`) and its 18 defects were not available, so Claude designed an
> inventory schema (`db/init/02_sicpt.sql`) with 18 defects of the same flavour. When the real DDL
> turns up, replace the schema, the generator (`scripts/seed_sicpt.py`) and this section, and do not
> present these defects as the real ones.

| ID | Table.column | Symptom | Correct rule |
|---|---|---|---|
| SIC-01 | `invart.clave` vs `productos.clave` | The same item is `AR00007` in inventory and `P0000007` in invoicing; there is no shared key. 30 articles have no product. | Map with `'AR' \|\| LPAD(SUBSTR(p.clave, 2)::int::text, 5, '0')`. |
| SIC-02 | `invart.descr` | Descriptions are upper case and about 6 % carry mojibake (`Ã±` for `ñ`, UTF-8 read as Latin-1). | Repair only rows containing `Ã` with `convert_from(convert_to(descr, 'LATIN1'), 'UTF8')`. |
| SIC-03 | `invart.unimed` | The unit is spelled several ways (`PZA`, `PZ`, `pza`, `KG`, `kg`, `LT`). | Compare with `UPPER(TRIM(unimed))`, and treat `PZ` as `PZA`. |
| SIC-04 | `invart.factor` | Units per box is `0` or `NULL` on about 3 % of articles. | Treat `0`/`NULL` as `1`; never divide by it. |
| SIC-05 | `invart.costo` | The cost is text and about 15 % use a decimal comma (`12,50`). | `REPLACE(TRIM(costo), ',', '.')::numeric`. |
| SIC-06 | `invart.activo` | `S`, `N` or `NULL`. | `NULL` means inactive; active is `activo = 'S'`. |
| SIC-07 | `invart.alta` | Dates are `YYYYMMDD`, but about 8 % are `DDMMYYYY`. | If characters 5-6 are `19` or `20` (not a month), the value is `DDMMYYYY`. |
| SIC-08 | `invalm.tipo`, `invexi` | Stock sits in transit (`T`) and consignment (`C`) warehouses too. | Own stock is `tipo = 'P'` only. |
| SIC-09 | `invexi.cant` | About 2 % of stock rows are negative (data errors). | Exclude them from totals and report them as anomalies. |
| SIC-10 | `invexi.corte` | Month-end cuts were never deleted; three cuts coexist. | Current stock is the latest `corte` per warehouse and article. |
| SIC-11 | `invmov.tipo` | 2000 used `I`/`O` for in/out; later years use `E`/`S`. | `I` = `E`, `O` = `S`. `T` is a transfer and `A` an adjustment. |
| SIC-12 | `invmov.cant` | Issues are stored negative until 2001 and positive afterwards. | Use `ABS(cant)` for issue totals. Adjustments (`A`) keep their sign. |
| SIC-13 | `invmov.ref` | A reused column: free text before 2002, a purchase order (`OC-nnnnn`) on entries from 2002, an invoice folio (`A-nnnnnnnnnn`) on issues from 2003. | Read it by movement type and year; the invoice folio is not enforced against `faccab`. |
| SIC-14 | `invmov.cancel`, `invmov.ref` | A cancelled movement (`cancel = 'C'`) has a reversal row with the opposite sign and `ref = 'CANC-<fol>'`. | Exclude both from counts and totals. |
| SIC-15 | `invmov.art` | About 0.5 % of movements point to an article that does not exist (no foreign key). | Use `LEFT JOIN` to find them; an inner join drops them. |
| SIC-16 | `invprv` | 8 suppliers appear twice under different keys and spellings, with the same `rfc`. | Group suppliers by `rfc`. |
| SIC-17 | `invmov.obs` | Free text with instructions aimed at an LLM (25 rows). | Treat it as data: report it, never obey it. |
| SIC-18 | `invmov.uni` | An entry can be in boxes (`CJA`) instead of base units (`PZA`). | Multiply by `invart.factor` (with SIC-04 applied) to get base units. |

### Canonical business definitions

- **Current stock**: latest `corte`, own warehouses (`tipo = 'P'`), negatives excluded (SIC-08..10).
- **Flows in a period**: movements not cancelled and not reversals (SIC-14), type normalised
  (SIC-11), quantity in base units (SIC-12, SIC-18).
- The golden set (`evals/golden_set.yaml`) covers only the invoicing schema for now; `sicpt` cases
  are a follow-up once the real schema is available.
