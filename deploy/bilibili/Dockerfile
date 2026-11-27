ARG VSA_BILIBILI_BASE
FROM ${VSA_BILIBILI_BASE}
USER root
COPY deploy/bilibili/requirements.txt /opt/bilibili/requirements.txt
RUN --mount=from=wheels,target=/wheels,readonly pip install --no-cache-dir --no-index --find-links=/wheels --no-deps --require-hashes -r /opt/bilibili/requirements.txt
COPY --from=app /bilibili_evidence.py /opt/bilibili/
COPY deploy/bilibili/egress.py deploy/bilibili/extract.py deploy/bilibili/runner.py /opt/bilibili/
USER 10001:10001
WORKDIR /opt/bilibili
ENTRYPOINT ["python", "-B"]
CMD ["/opt/bilibili/runner.py"]
