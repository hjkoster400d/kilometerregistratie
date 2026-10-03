ARG BUILD_FROM
FROM ${BUILD_FROM}

# Python + build deps on the HA base (Alpine). Build toolchain is needed for
# uvicorn[standard] native extensions (httptools, uvloop); kept minimal.
RUN apk add --no-cache python3 py3-pip gcc musl-dev python3-dev libffi-dev

WORKDIR /app

# Install Python dependencies into the system environment (add-on container is single-purpose).
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# App code.
COPY app ./app

# Add-on entrypoint.
COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
