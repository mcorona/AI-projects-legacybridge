# One image, three Lambda handlers (front, worker, db init); the stack picks one with CMD (ADR-014).
# Built for arm64: Graviton costs less per millisecond. No credential is baked in: the functions
# read their secrets from Secrets Manager when they start.
FROM public.ecr.aws/lambda/python:3.12

# The same uv as CI, so the image installs exactly what uv.lock says.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && uv pip install --python /var/lang/bin/python --target "${LAMBDA_TASK_ROOT}" --no-cache \
      -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# The package is imported from source: models.yaml, swar.yaml, db/init and the seed script are found
# relative to it, exactly as in the repository.
COPY src ./src
COPY config ./config
COPY db ./db
COPY scripts ./scripts
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/src:${LAMBDA_TASK_ROOT}

CMD ["legacybridge.aws.worker.handler"]
