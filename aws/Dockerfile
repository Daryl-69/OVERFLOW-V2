# One image for every Upstream Lambda; each function picks its handler via
# ImageConfig.Command in template.yaml. Build context is the repo root.
FROM public.ecr.aws/lambda/python:3.12

COPY pyproject.toml README.md ${LAMBDA_TASK_ROOT}/src/
COPY upstream ${LAMBDA_TASK_ROOT}/src/upstream
RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}/src[agent]" --target "${LAMBDA_TASK_ROOT}" \
 && rm -rf "${LAMBDA_TASK_ROOT}/src"

# The live ward (Indore by default) is built once here, not on every cold start.
ARG SCENARIO=data/scenarios/bhagirathpura.json
COPY data/streets ${LAMBDA_TASK_ROOT}/data/streets
COPY ${SCENARIO} /tmp/scenario.json
RUN cd ${LAMBDA_TASK_ROOT} && python -m upstream.ward /tmp/scenario.json ${LAMBDA_TASK_ROOT}/ward.pkl \
 && rm /tmp/scenario.json

ENV UPSTREAM_WARD_PATH=${LAMBDA_TASK_ROOT}/ward.pkl \
    UPSTREAM_TZ=Asia/Kolkata \
    MPLCONFIGDIR=/tmp

CMD ["upstream.cloud.handlers.api"]
