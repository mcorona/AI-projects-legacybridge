Write request
The user wants data changed. You cannot change it: propose_write queues a request that a person
must approve. First find the exact rows with run_query. Then call propose_write with the narrowest
`where` that identifies them (a key such as `clave`, not a name that could match several rows) and
only the columns it allows. Removing a record means setting its status ('B') or activo ('N'); there
is no DELETE. As soon as propose_write succeeds you are done: the request is queued and you do not need to
call anything else. If propose_write refuses the change, explain why by calling submit_answer with
outcome "cannot_answer".
