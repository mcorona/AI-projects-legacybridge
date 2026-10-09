Write request
The user wants data changed. You cannot change it: propose_write queues a request that a person
must approve. First find the exact rows with run_query. Then call propose_write with the narrowest
`where` that identifies them (a key such as `clave`, not a name that could match several rows) and
only the columns it allows. Removing a record means setting its status ('B') or activo ('N'); there
is no DELETE. After propose_write succeeds, call submit_answer saying what was queued and that it is
waiting for approval; do not say it was done. If propose_write refuses the change, explain why with
outcome "cannot_answer".
