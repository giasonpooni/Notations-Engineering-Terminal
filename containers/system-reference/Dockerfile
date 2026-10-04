# Supply an immutable Python base reference at build time.  The resulting
# worker image must also be selected by digest for execution.
ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG PYTHON_BASE
RUN python -c 'import os, re, sys; assert re.fullmatch(r"python:3\.12\.14-slim@sha256:[a-f0-9]{64}", os.environ["PYTHON_BASE"]); assert sys.version_info[:3] == (3, 12, 14)'

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONHASHSEED=0
COPY dist/*.whl /tmp/wheels/
RUN python -m pip install --no-cache-dir numpy==2.4.3 websockets==16.0 \
    && python -m pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

WORKDIR /work
USER 65534:65534
CMD ["python", "-m", "ciw", "system", "worker", "/work/spec.json", "--output", "/work/candidate.json"]
