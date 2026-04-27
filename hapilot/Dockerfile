ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python and pip from Alpine
RUN apk add --no-cache \
    python3 \
    py3-pip

# Copy app rootfs into container
COPY rootfs /

WORKDIR /app

# Install Python deps (use --break-system-packages on Alpine 3.18+)
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Make entrypoint executable
RUN chmod +x /run.sh

CMD ["/run.sh"]
