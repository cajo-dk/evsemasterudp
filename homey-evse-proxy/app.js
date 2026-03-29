const Homey = require('homey');
const dgram = require('node:dgram');
const mqtt = require('mqtt');

const SETTINGS_KEY = 'proxyConfig';
const APP_VERSION = '0.1.0';
const LISTEN_PORT = 28376;
const COMMAND = {
  LOGIN_BROADCAST: 0x0001,
  LOGIN_RESPONSE: 0x0002,
  HEADING: 0x0003,
  STATUS: 0x0004,
  CHARGE_START_RESPONSE: 0x0007,
  CHARGE_STOP_RESPONSE: 0x0008,
  PASSWORD_ERROR: 0x0155,
  LOGIN_CONFIRM: 0x8001,
  REQUEST_LOGIN: 0x8002,
  HEADING_RESPONSE: 0x8003,
  STATUS_RESPONSE: 0x8004,
  CHARGE_START: 0x8007,
  CHARGE_STOP: 0x8008,
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readCString(buffer, start, length) {
  return buffer.subarray(start, start + length).toString('ascii').replace(/\x00+$/, '');
}

function readTemperature(buffer, offset) {
  if (buffer.length < offset + 2) {
    return -1;
  }
  const raw = buffer.readUInt16BE(offset);
  if (raw === 0xffff) {
    return -1;
  }
  return Math.round((raw - 20000) * 0.01 * 100) / 100;
}

function packDatagram({ serialHex, password, command, payload = Buffer.alloc(0), keyType = 0 }) {
  const totalLength = 25 + payload.length;
  const buffer = Buffer.alloc(totalLength);

  buffer.writeUInt16BE(0x0601, 0);
  buffer.writeUInt16BE(totalLength, 2);
  buffer.writeUInt8(keyType, 4);

  if (serialHex) {
    Buffer.from(serialHex, 'hex').copy(buffer, 5, 0, 8);
  }

  if (password !== undefined && password !== null) {
    Buffer.from(String(password), 'ascii').subarray(0, 6).copy(buffer, 13);
  }

  buffer.writeUInt16BE(command, 19);
  payload.copy(buffer, 21);

  let checksum = 0;
  for (let index = 0; index < totalLength - 4; index += 1) {
    checksum = (checksum + buffer[index]) % 0xffff;
  }

  buffer.writeUInt16BE(checksum, totalLength - 4);
  buffer.writeUInt16BE(0x0f02, totalLength - 2);
  return buffer;
}

function parseDatagrams(buffer) {
  const datagrams = [];
  let offset = 0;

  while (offset + 25 <= buffer.length) {
    if (buffer.readUInt16BE(offset) !== 0x0601) {
      break;
    }

    const totalLength = buffer.readUInt16BE(offset + 2);
    if (offset + totalLength > buffer.length || totalLength < 25) {
      break;
    }

    const command = buffer.readUInt16BE(offset + 19);
    const serial = buffer.subarray(offset + 5, offset + 13).toString('hex');
    const passwordBytes = buffer.subarray(offset + 13, offset + 19);
    const password = passwordBytes.every((byte) => byte === 0)
      ? null
      : passwordBytes.toString('ascii').replace(/\x00+$/, '');
    const payload = buffer.subarray(offset + 21, offset + totalLength - 4);

    datagrams.push({
      command,
      serial,
      password,
      payload,
    });

    offset += totalLength;
  }

  return datagrams;
}

class EVSEProxySession {
  constructor(homey) {
    this.homey = homey;
    this.socket = null;
    this.mqttClient = null;
    this.mqttConnected = false;
    this.config = this.normalizeConfig(homey.settings.get(SETTINGS_KEY) || {});
    this.evse = {
      serial: this.config.serial || null,
      ip: this.config.host || null,
      port: this.config.port || LISTEN_PORT,
      brand: '',
      model: '',
      hardwareVersion: '',
      maxPower: null,
      maxElectricity: null,
      hotline: '',
      endpointLocked: Boolean(this.config.host),
    };
    this.loggedIn = false;
    this.lastSeen = null;
    this.lastStatus = null;
    this.lastStatusAt = null;
    this.authFailureReason = null;
    this.responseBuffer = [];
    this.keepAliveTimer = null;
    this.publishTimers = {
      health: null,
      diagnostics: null,
      metadata: null,
    };
    this.lastPublishedAt = {
      health: 0,
      diagnostics: 0,
      metadata: 0,
    };
    this.discoveryPublished = false;
    this.connectPromise = null;
    this.chargingStateOverride = null;
    this.chargingStateOverrideTimer = null;
  }

  log(message, details = null) {
    if (details === null || details === undefined) {
      this.homey.log(`[EVSE Proxy] ${message}`);
      return;
    }
    this.homey.log(`[EVSE Proxy] ${message}`, details);
  }

  error(message, error = null) {
    if (!error) {
      this.homey.error(`[EVSE Proxy] ${message}`);
      return;
    }
    this.homey.error(
      `[EVSE Proxy] ${message}: ${error && error.message ? error.message : error}`,
    );
  }

  normalizeConfig(config) {
    return {
      serial: config.serial || '',
      password: config.password || '',
      host: config.host || '',
      port: Number(config.port) || LISTEN_PORT,
      autoConnect: config.autoConnect !== false,
      mqttBrokerUrl: config.mqttBrokerUrl || '',
      mqttUsername: config.mqttUsername || '',
      mqttPassword: config.mqttPassword || '',
      mqttBaseTopic: config.mqttBaseTopic || 'homey/evse_master_proxy',
      mqttClientId: config.mqttClientId || 'homey-evse-master-proxy',
    };
  }

  async start() {
    if (!this.socket) {
      this.socket = dgram.createSocket('udp4');
      this.socket.on('error', (error) => this.error('UDP socket error', error));
      this.socket.on('message', (message, rinfo) => this.handleMessage(message, rinfo));

      await new Promise((resolve, reject) => {
        this.socket.once('listening', resolve);
        this.socket.once('error', reject);
        this.socket.bind(LISTEN_PORT);
      });

      this.socket.setBroadcast(true);
      this.log(`UDP listener bound to port ${LISTEN_PORT}`);
    }

    await this.ensureMqtt();
    this.schedulePublish('health');
    this.schedulePublish('diagnostics');

    if (this.config.autoConnect && this.config.serial && this.config.password) {
      this.log('Auto-connect scheduled');
      this.homey.setTimeout(() => {
        this.connect().catch((error) => this.error('Auto-connect failed', error));
      }, 1000);
    }
  }

  async stop() {
    this.clearKeepAlive();
    this.clearPublishTimers();
    await this.stopMqtt();
    if (!this.socket) {
      return;
    }

    const socket = this.socket;
    this.socket = null;
    await new Promise((resolve) => socket.close(resolve));
    this.log('UDP listener stopped');
  }

  getConfig() {
    return { ...this.config };
  }

  async updateConfig(nextConfig) {
    const previousBroker = this.config.mqttBrokerUrl;
    const previousClientId = this.config.mqttClientId;
    this.config = this.normalizeConfig({ ...this.config, ...nextConfig });
    this.evse.serial = this.config.serial || null;
    this.evse.ip = this.config.host || this.evse.ip;
    this.evse.port = this.config.port;
    this.evse.endpointLocked = Boolean(this.config.host);
    await this.homey.settings.set(SETTINGS_KEY, this.config);
    this.loggedIn = false;
    this.authFailureReason = null;
    this.discoveryPublished = false;

    this.log('Configuration updated', {
      serial: this.config.serial || null,
      host: this.config.host || null,
      port: this.config.port,
      autoConnect: this.config.autoConnect,
      mqttBrokerUrl: this.config.mqttBrokerUrl || null,
      mqttBaseTopic: this.config.mqttBaseTopic || null,
    });

    if (
      previousBroker !== this.config.mqttBrokerUrl ||
      previousClientId !== this.config.mqttClientId
    ) {
      await this.restartMqtt();
    } else {
      await this.ensureMqtt();
    }

    this.schedulePublish('health');
    this.schedulePublish('diagnostics');
    return this.getConfig();
  }

  getDiagnostics() {
    return {
      config: {
        ...this.config,
        password: this.config.password ? '******' : '',
        mqttPassword: this.config.mqttPassword ? '******' : '',
      },
      endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      serial: this.evse.serial,
      loggedIn: this.loggedIn,
      effectiveLoggedIn: this.effectiveLoggedIn(),
      mqttConnected: this.mqttConnected,
      authFailureReason: this.authFailureReason,
      lastSeen: this.lastSeen,
      lastStatusAt: this.lastStatusAt,
      metadata: {
        brand: this.evse.brand,
        model: this.evse.model,
        hardwareVersion: this.evse.hardwareVersion,
        maxPower: this.evse.maxPower,
        maxElectricity: this.evse.maxElectricity,
        hotline: this.evse.hotline,
      },
      status: this.lastStatus,
    };
  }

  getStatus() {
    return {
      serial: this.evse.serial,
      endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      loggedIn: this.loggedIn,
      effectiveLoggedIn: this.effectiveLoggedIn(),
      mqttConnected: this.mqttConnected,
      authFailureReason: this.authFailureReason,
      lastSeen: this.lastSeen,
      lastStatusAt: this.lastStatusAt,
      status: this.lastStatus,
    };
  }

  async connect() {
    if (this.connectPromise) {
      this.log('Connect requested while another connect is already in progress');
      return this.connectPromise;
    }

    this.connectPromise = this.connectInternal();
    try {
      return await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async connectInternal() {
    if (!this.config.serial || !this.config.password) {
      throw new Error('serial and password must be configured');
    }

    await this.start();
    this.authFailureReason = null;
    this.schedulePublish('health');
    this.schedulePublish('diagnostics');

    if (this.config.host) {
      this.evse.ip = this.config.host;
      this.evse.port = this.config.port;
      this.evse.endpointLocked = true;
      this.log(`Connecting in direct mode to ${this.evse.ip}:${this.evse.port}`);
    } else {
      this.log('Waiting for EVSE discovery broadcast');
      await this.waitForDiscovery(10000);
      this.log(`Discovered EVSE at ${this.evse.ip}:${this.evse.port}`);
    }

    const loginResponse = await this.loginOnce();
    if (!loginResponse || loginResponse.command !== COMMAND.LOGIN_RESPONSE) {
      this.loggedIn = false;
      this.clearKeepAlive();
      this.log('EVSE login failed', {
        reason: this.authFailureReason,
        endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      });
      this.schedulePublish('health');
      this.schedulePublish('diagnostics');
      return this.getDiagnostics();
    }

    this.loggedIn = true;
    this.authFailureReason = null;
    this.log(`EVSE login succeeded at ${this.evse.ip}:${this.evse.port}`);
    await this.sendHeading();
    this.startKeepAlive();
    this.schedulePublish('health');
    this.schedulePublish('diagnostics');
    this.schedulePublish('metadata');
    await this.publishHomeAssistantDiscovery();
    return this.getDiagnostics();
  }

  async refreshStatus() {
    if (!this.loggedIn) {
      this.log('Refresh requested while disconnected; reconnecting first');
      await this.connect();
    } else {
      this.log('Refresh requested; sending heading');
      await this.sendHeading();
    }

    await sleep(250);
    this.schedulePublish('health');
    this.schedulePublish('diagnostics');
    return this.getStatus();
  }

  async waitForDiscovery(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (this.evse.ip && this.evse.port) {
        return;
      }
      await sleep(100);
    }
    this.authFailureReason = 'discovery_timeout';
    this.log('Timed out waiting for EVSE discovery');
    throw new Error('timed out waiting for EVSE discovery broadcast');
  }

  async loginOnce() {
    this.responseBuffer = [];
    const loginRequest = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.REQUEST_LOGIN,
      payload: Buffer.from([0x00]),
    });

    await this.sendToEvse(loginRequest);
    this.log(`RequestLogin sent to ${this.evse.ip}:${this.evse.port}`);

    const response = await this.waitForResponse(
      [COMMAND.LOGIN_RESPONSE, COMMAND.PASSWORD_ERROR],
      3000,
    );

    if (!response) {
      this.authFailureReason = 'no_login_response';
      this.log('No login response received from EVSE');
      return null;
    }

    if (response.command === COMMAND.PASSWORD_ERROR) {
      this.authFailureReason = 'incorrect_password';
      this.log('EVSE reported incorrect password');
      return response;
    }

    const loginConfirm = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.LOGIN_CONFIRM,
      payload: Buffer.from([0x00]),
    });

    await this.sendToEvse(loginConfirm);
    this.log('LoginConfirm sent to EVSE');
    return response;
  }

  async sendHeading() {
    const heading = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.HEADING,
      payload: Buffer.alloc(0),
    });
    await this.sendToEvse(heading);
    this.log('Heading sent to EVSE');
  }

  normalizeChargingCommand(payload) {
    const value = String(payload || '').trim().toLowerCase();
    if (['1', 'on', 'true', 'start', 'charging'].includes(value)) {
      return true;
    }
    if (['0', 'off', 'false', 'stop', 'idle'].includes(value)) {
      return false;
    }
    return null;
  }

  isPluggedIn(status = this.lastStatus) {
    if (!status) {
      return false;
    }
    // Field observations show gunState=2 can still mean unplugged/idle on some units.
    // Treat only the higher states as truly plugged to avoid false positives.
    return [3, 4].includes(status.gunState);
  }

  isCharging(status = this.lastStatus) {
    if (!status) {
      return false;
    }
    return status.outputState === 1;
  }

  hasRecentEvseTraffic(maxAgeMs = 180000) {
    if (!this.lastSeen) {
      return false;
    }
    const lastSeenMs = Date.parse(this.lastSeen);
    if (Number.isNaN(lastSeenMs)) {
      return false;
    }
    return (Date.now() - lastSeenMs) <= maxAgeMs;
  }

  effectiveLoggedIn() {
    if (this.loggedIn) {
      return true;
    }

    if (['incorrect_password', 'discovery_timeout'].includes(this.authFailureReason)) {
      return false;
    }

    return this.hasRecentEvseTraffic();
  }

  clearChargingStateOverride() {
    this.chargingStateOverride = null;
    if (this.chargingStateOverrideTimer) {
      this.homey.clearTimeout(this.chargingStateOverrideTimer);
      this.chargingStateOverrideTimer = null;
    }
  }

  setChargingStateOverride(enabled, timeoutMs = 30000) {
    this.clearChargingStateOverride();
    this.chargingStateOverride = {
      value: Boolean(enabled),
      expiresAt: Date.now() + timeoutMs,
    };
    this.chargingStateOverrideTimer = this.homey.setTimeout(() => {
      this.clearChargingStateOverride();
      if (this.lastStatus) {
        this.publishStatus().catch((error) => this.error('Publish status failed', error));
      }
    }, timeoutMs);
  }

  effectiveChargingState(status = this.lastStatus) {
    const actualCharging = this.isCharging(status);
    if (!this.chargingStateOverride) {
      return actualCharging;
    }

    if (actualCharging === this.chargingStateOverride.value) {
      this.clearChargingStateOverride();
      return actualCharging;
    }

    if (Date.now() < this.chargingStateOverride.expiresAt) {
      return this.chargingStateOverride.value;
    }

    this.clearChargingStateOverride();
    return actualCharging;
  }

  deriveEvseState(status = this.lastStatus) {
    if (!status) {
      return this.loggedIn ? 'unknown' : 'disconnected';
    }
    if (status.errorBits) {
      return 'error';
    }
    if (this.effectiveChargingState(status)) {
      return 'plugged_charging';
    }
    if (this.isPluggedIn(status)) {
      return 'plugged_idle';
    }
    return 'unplugged_idle';
  }

  currentChargeAmps() {
    const discoveredMax = Number(this.evse.maxElectricity) || 0;
    if (discoveredMax >= 6) {
      return Math.min(discoveredMax, 16);
    }
    return 16;
  }

  buildChargeStartPayload() {
    const payload = Buffer.alloc(47);
    const chargeId = `${Math.floor(Date.now() / 1000)}`.slice(0, 12).padEnd(16, '0');
    const userId = 'homey';
    const reservationDate = Math.floor(Date.now() / 1000);

    payload[0] = 1;
    Buffer.from(userId, 'ascii').copy(payload, 1, 0, 16);
    Buffer.from(chargeId, 'ascii').copy(payload, 17, 0, 16);
    payload[33] = 0;
    payload.writeUInt32BE(reservationDate, 34);
    payload[38] = 1;
    payload[39] = 1;
    payload.writeUInt16BE(0xffff, 40);
    payload.writeUInt16BE(0xffff, 42);
    payload.writeUInt16BE(0xffff, 44);
    payload[46] = this.currentChargeAmps();
    return payload;
  }

  async sendChargeStart() {
    const chargeStart = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.CHARGE_START,
      payload: this.buildChargeStartPayload(),
    });
    await this.sendToEvse(chargeStart);
    this.log('Charge start sent to EVSE');
  }

  async sendChargeStop() {
    const chargeStop = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.CHARGE_STOP,
      payload: Buffer.alloc(0),
    });
    await this.sendToEvse(chargeStop);
    this.log('Charge stop sent to EVSE');
  }

  async setChargingEnabled(enabled) {
    if (enabled) {
      if (!this.loggedIn) {
        await this.connect();
      }
      await this.sendChargeStart();
      this.setChargingStateOverride(true);
      if (this.lastStatus) {
        await this.publishStatus();
      }
      return;
    }

    if (!this.loggedIn) {
      this.log('Ignoring stop command because EVSE is not logged in');
      return;
    }
    await this.sendChargeStop();
    this.setChargingStateOverride(false);
    if (this.lastStatus) {
      await this.publishStatus();
    }
  }

  startKeepAlive() {
    this.clearKeepAlive();
    this.keepAliveTimer = this.homey.setInterval(() => {
      if (!this.loggedIn) {
        return;
      }
      this.sendHeading().catch((error) => this.error('Keepalive failed', error));
    }, 20000);
    this.log('Keepalive loop started');
  }

  clearKeepAlive() {
    if (this.keepAliveTimer) {
      this.homey.clearInterval(this.keepAliveTimer);
      this.keepAliveTimer = null;
      this.log('Keepalive loop stopped');
    }
  }

  clearPublishTimers() {
    for (const key of Object.keys(this.publishTimers)) {
      if (this.publishTimers[key]) {
        this.homey.clearTimeout(this.publishTimers[key]);
        this.publishTimers[key] = null;
      }
    }
  }

  async waitForResponse(commands, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const index = this.responseBuffer.findIndex((item) => commands.includes(item.command));
      if (index !== -1) {
        return this.responseBuffer.splice(index, 1)[0];
      }
      await sleep(100);
    }
    return null;
  }

  async sendToEvse(buffer) {
    if (!this.socket) {
      throw new Error('UDP socket not started');
    }
    if (!this.evse.ip || !this.evse.port) {
      throw new Error('EVSE endpoint is unknown');
    }

    await new Promise((resolve, reject) => {
      this.socket.send(buffer, this.evse.port, this.evse.ip, (error) => {
        if (error) {
          reject(error);
        } else {
          resolve();
        }
      });
    });
  }

  handleMessage(message, rinfo) {
    const datagrams = parseDatagrams(message);
    for (const datagram of datagrams) {
      if (this.config.serial && datagram.serial && datagram.serial !== this.config.serial) {
        continue;
      }

      this.lastSeen = new Date().toISOString();

      if (!this.evse.endpointLocked) {
        this.evse.ip = rinfo.address;
        this.evse.port = rinfo.port;
      }

      if (!this.evse.serial) {
        this.evse.serial = datagram.serial;
      }

      this.responseBuffer.push(datagram);
      this.log(
        `Received datagram 0x${datagram.command.toString(16).padStart(4, '0')} from ${rinfo.address}:${rinfo.port}`,
      );

      if (
        datagram.command === COMMAND.LOGIN_BROADCAST ||
        datagram.command === COMMAND.LOGIN_RESPONSE
      ) {
        this.updateMetadata(datagram.payload);
        this.log('Updated EVSE metadata', {
          brand: this.evse.brand,
          model: this.evse.model,
          hardwareVersion: this.evse.hardwareVersion,
          maxPower: this.evse.maxPower,
          maxElectricity: this.evse.maxElectricity,
        });
        this.schedulePublish('metadata');
        this.publishHomeAssistantDiscovery().catch((error) => {
          this.error('Publish Home Assistant discovery failed', error);
        });
      }

      if (datagram.command === COMMAND.STATUS) {
        this.lastStatus = this.parseStatus(datagram.payload);
        this.lastStatusAt = new Date().toISOString();
        this.log('Received EVSE status update', this.lastStatus);
        this.sendStatusAck().catch((error) => this.error('Status ACK failed', error));
        this.publishStatus().catch((error) => this.error('Publish status failed', error));
      }

      this.schedulePublish('health');
      this.schedulePublish('diagnostics');
    }
  }

  updateMetadata(payload) {
    if (payload.length < 54) {
      return;
    }
    this.evse.brand = readCString(payload, 1, 16);
    this.evse.model = readCString(payload, 17, 16);
    this.evse.hardwareVersion = readCString(payload, 33, 16);
    this.evse.maxPower = payload.readUInt32BE(49);
    this.evse.maxElectricity = payload[53];
    this.evse.hotline = payload.length >= 70 ? readCString(payload, 54, 16) : '';
  }

  parseStatus(payload) {
    if (payload.length < 25) {
      return null;
    }

    return {
      lineId: payload[0],
      l1Voltage: payload.readUInt16BE(1) * 0.1,
      l1Electricity: payload.readUInt16BE(3) * 0.01,
      currentPower: payload.readUInt32BE(5),
      totalKwhCounter: payload.readUInt32BE(9) * 0.01,
      innerTemp: readTemperature(payload, 13),
      outerTemp: readTemperature(payload, 15),
      emergencyButtonState: payload[17],
      gunState: payload[18],
      outputState: payload[19],
      currentState: payload[20],
      errorBits: payload.readUInt32BE(21),
    };
  }

  async sendStatusAck() {
    if (!this.config.serial || !this.config.password || !this.evse.ip || !this.evse.port) {
      return;
    }
    const ack = packDatagram({
      serialHex: this.config.serial,
      password: this.config.password,
      command: COMMAND.STATUS_RESPONSE,
      payload: Buffer.from([0x01]),
    });
    await this.sendToEvse(ack);
    this.log('Sent status ACK to EVSE');
  }

  async restartMqtt() {
    await this.stopMqtt();
    await this.ensureMqtt();
  }

  async ensureMqtt() {
    if (!this.config.mqttBrokerUrl) {
      this.mqttConnected = false;
      this.log('MQTT disabled because no broker URL is configured');
      return;
    }
    if (this.mqttClient) {
      return;
    }

    const options = {
      clientId: this.config.mqttClientId || undefined,
      username: this.config.mqttUsername || undefined,
      password: this.config.mqttPassword || undefined,
      reconnectPeriod: 5000,
      connectTimeout: 10000,
    };

    this.log(`Connecting to MQTT broker ${this.config.mqttBrokerUrl}`);
    this.mqttClient = mqtt.connect(this.config.mqttBrokerUrl, options);
    this.mqttClient.on('connect', () => {
      this.mqttConnected = true;
      this.log(`MQTT connected to ${this.config.mqttBrokerUrl}`);
      this.subscribeMqttTopics().catch((error) => this.error('MQTT subscribe failed', error));
      this.schedulePublish('health');
      this.schedulePublish('diagnostics');
      if (this.evse.brand) {
        this.schedulePublish('metadata');
      }
      if (this.lastStatus) {
        this.publishStatus().catch((error) => this.error('Publish status failed', error));
      }
      this.publishHomeAssistantDiscovery().catch((error) => {
        this.error('Publish Home Assistant discovery failed', error);
      });
    });
    this.mqttClient.on('reconnect', () => {
      this.log('MQTT reconnecting');
    });
    this.mqttClient.on('close', () => {
      this.mqttConnected = false;
      this.log('MQTT connection closed');
    });
    this.mqttClient.on('error', (error) => {
      this.mqttConnected = false;
      this.error('MQTT error', error);
    });
    this.mqttClient.on('message', (topic, payload) => {
      this.handleMqttMessage(topic, payload).catch((error) => {
        this.error(`MQTT command handling failed for ${topic}`, error);
      });
    });
  }

  async stopMqtt() {
    if (!this.mqttClient) {
      this.mqttConnected = false;
      return;
    }

    const client = this.mqttClient;
    this.mqttClient = null;
    this.mqttConnected = false;
    await new Promise((resolve) => client.end(false, {}, resolve));
    this.log('MQTT client stopped');
  }

  topicFor(suffix) {
    return `${this.config.mqttBaseTopic.replace(/\/+$/, '')}/${suffix}`;
  }

  commandTopicFor(suffix) {
    return `${this.config.mqttBaseTopic.replace(/\/+$/, '')}/command/${suffix}`;
  }

  discoveryTopic(component, objectId) {
    return `homeassistant/${component}/${objectId}/config`;
  }

  discoveryObjectId(key) {
    const serial = this.evse.serial || this.config.serial || 'unknown';
    return `evse_master_proxy_${serial}_${key}`;
  }

  deviceDescriptor() {
    const serial = this.evse.serial || this.config.serial || 'unknown';
    return {
      identifiers: [`evse_master_proxy_${serial}`],
      name: `EVSE Master ${serial}`,
      manufacturer: this.evse.brand || 'EVSE Master',
      model: this.evse.model || 'EVSE Charger',
      hw_version: this.evse.hardwareVersion || undefined,
      sw_version: APP_VERSION,
    };
  }

  async publishJson(topic, payload, options = {}) {
    if (!this.mqttClient || !this.mqttConnected) {
      return;
    }

    await new Promise((resolve, reject) => {
      this.mqttClient.publish(
        topic,
        JSON.stringify(payload),
        {
          qos: 1,
          retain: true,
          ...options,
        },
        (error) => {
          if (error) {
            reject(error);
          } else {
            resolve();
          }
        },
      );
    });
    this.log(`Published MQTT topic ${topic}`);
  }

  async publishDiscoveryEntity(component, key, payload) {
    await this.publishJson(
      this.discoveryTopic(component, this.discoveryObjectId(key)),
      payload,
      { qos: 1, retain: true },
    );
  }

  async publishHomeAssistantDiscovery() {
    if (!this.mqttClient || !this.mqttConnected) {
      return;
    }

    const baseTopic = this.config.mqttBaseTopic.replace(/\/+$/, '');
    const device = this.deviceDescriptor();
    const healthTopic = `${baseTopic}/health`;
    const statusTopic = `${baseTopic}/status`;
    const metadataTopic = `${baseTopic}/metadata`;
    const chargingCommandTopic = this.commandTopicFor('charging');
    const commonAvailability = {
      availability_topic: healthTopic,
      availability_template: "{{ 'online' if value_json.mqttConnected else 'offline' }}",
      payload_available: 'online',
      payload_not_available: 'offline',
    };

    const entities = [
      ['sensor', 'current_power', {
        name: 'EVSE Current Power',
        unique_id: this.discoveryObjectId('current_power'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.currentPower }}",
        unit_of_measurement: 'W',
        device_class: 'power',
        state_class: 'measurement',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'voltage_l1', {
        name: 'EVSE Voltage L1',
        unique_id: this.discoveryObjectId('voltage_l1'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.l1Voltage }}",
        unit_of_measurement: 'V',
        device_class: 'voltage',
        state_class: 'measurement',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'current_l1', {
        name: 'EVSE Current L1',
        unique_id: this.discoveryObjectId('current_l1'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.l1Electricity }}",
        unit_of_measurement: 'A',
        device_class: 'current',
        state_class: 'measurement',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'energy_total', {
        name: 'EVSE Energy Total',
        unique_id: this.discoveryObjectId('energy_total'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.totalKwhCounter }}",
        unit_of_measurement: 'kWh',
        device_class: 'energy',
        state_class: 'total_increasing',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'inner_temperature', {
        name: 'EVSE Inner Temperature',
        unique_id: this.discoveryObjectId('inner_temperature'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.innerTemp }}",
        unit_of_measurement: '°C',
        device_class: 'temperature',
        state_class: 'measurement',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'outer_temperature', {
        name: 'EVSE Outer Temperature',
        unique_id: this.discoveryObjectId('outer_temperature'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.outerTemp }}",
        unit_of_measurement: '°C',
        device_class: 'temperature',
        state_class: 'measurement',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'gun_state', {
        name: 'EVSE Gun State',
        unique_id: this.discoveryObjectId('gun_state'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.gunState }}",
        device,
        ...commonAvailability,
      }],
      ['sensor', 'output_state', {
        name: 'EVSE Output State',
        unique_id: this.discoveryObjectId('output_state'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.outputState }}",
        device,
        ...commonAvailability,
      }],
      ['sensor', 'current_state', {
        name: 'EVSE Current State',
        unique_id: this.discoveryObjectId('current_state'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.currentState }}",
        device,
        ...commonAvailability,
      }],
      ['sensor', 'error_bits', {
        name: 'EVSE Error Bits',
        unique_id: this.discoveryObjectId('error_bits'),
        state_topic: statusTopic,
        value_template: "{{ value_json.status.errorBits }}",
        device,
        ...commonAvailability,
      }],
      ['sensor', 'evse_state', {
        name: 'EVSE State',
        unique_id: this.discoveryObjectId('evse_state'),
        state_topic: statusTopic,
        value_template: "{{ value_json.summary.evseState }}",
        icon: 'mdi:ev-station',
        device,
        ...commonAvailability,
      }],
      ['switch', 'charging', {
        name: 'EVSE Charging',
        unique_id: this.discoveryObjectId('charging'),
        state_topic: statusTopic,
        value_template: "{{ 'ON' if value_json.summary.charging else 'OFF' }}",
        command_topic: chargingCommandTopic,
        payload_on: 'ON',
        payload_off: 'OFF',
        icon: 'mdi:ev-plug-type2',
        device,
        ...commonAvailability,
      }],
      ['sensor', 'failure_reason', {
        name: 'EVSE Proxy Failure Reason',
        unique_id: this.discoveryObjectId('failure_reason'),
        state_topic: healthTopic,
        value_template: "{{ value_json.authFailureReason | default('none', true) }}",
        icon: 'mdi:alert-circle-outline',
        entity_category: 'diagnostic',
        device,
      }],
      ['sensor', 'brand', {
        name: 'EVSE Brand',
        unique_id: this.discoveryObjectId('brand'),
        state_topic: metadataTopic,
        value_template: "{{ value_json.brand }}",
        entity_category: 'diagnostic',
        device,
      }],
      ['sensor', 'model', {
        name: 'EVSE Model',
        unique_id: this.discoveryObjectId('model'),
        state_topic: metadataTopic,
        value_template: "{{ value_json.model }}",
        entity_category: 'diagnostic',
        device,
      }],
      ['sensor', 'hardware_version', {
        name: 'EVSE Hardware Version',
        unique_id: this.discoveryObjectId('hardware_version'),
        state_topic: metadataTopic,
        value_template: "{{ value_json.hardwareVersion }}",
        entity_category: 'diagnostic',
        device,
      }],
      ['binary_sensor', 'logged_in', {
        name: 'EVSE Proxy Logged In',
        unique_id: this.discoveryObjectId('logged_in'),
        state_topic: healthTopic,
        value_template: "{{ value_json.effectiveLoggedIn | string | lower }}",
        payload_on: 'true',
        payload_off: 'false',
        device_class: 'connectivity',
        device,
      }],
      ['binary_sensor', 'mqtt_connected', {
        name: 'EVSE Proxy MQTT Connected',
        unique_id: this.discoveryObjectId('mqtt_connected'),
        state_topic: healthTopic,
        value_template: "{{ value_json.mqttConnected | string | lower }}",
        payload_on: 'true',
        payload_off: 'false',
        device_class: 'connectivity',
        device,
      }],
    ];

    for (const [component, key, payload] of entities) {
      await this.publishDiscoveryEntity(component, key, payload);
    }

    if (!this.discoveryPublished) {
      this.log('Published Home Assistant MQTT discovery payloads');
    }
    this.discoveryPublished = true;
  }

  async subscribeMqttTopics() {
    if (!this.mqttClient || !this.mqttConnected) {
      return;
    }

    const topics = [this.commandTopicFor('charging')];
    await new Promise((resolve, reject) => {
      this.mqttClient.subscribe(topics, { qos: 1 }, (error) => {
        if (error) {
          reject(error);
        } else {
          resolve();
        }
      });
    });
    this.log('Subscribed to MQTT command topics', topics);
  }

  async handleMqttMessage(topic, payloadBuffer) {
    const chargingTopic = this.commandTopicFor('charging');
    if (topic !== chargingTopic) {
      return;
    }

    const payload = payloadBuffer.toString('utf8');
    const enabled = this.normalizeChargingCommand(payload);
    if (enabled === null) {
      this.log(`Ignoring unsupported charging command payload: ${payload}`);
      return;
    }

    this.log(`Received MQTT charging command: ${enabled ? 'ON' : 'OFF'}`);
    await this.setChargingEnabled(enabled);
  }

  schedulePublish(kind) {
    const minIntervalMs = 5000;
    const now = Date.now();
    const elapsed = now - this.lastPublishedAt[kind];

    if (elapsed >= minIntervalMs && !this.publishTimers[kind]) {
      this.publishKind(kind).catch((error) => this.error(`Publish ${kind} failed`, error));
      return;
    }

    if (this.publishTimers[kind]) {
      return;
    }

    const delay = Math.max(0, minIntervalMs - elapsed);
    this.publishTimers[kind] = this.homey.setTimeout(() => {
      this.publishTimers[kind] = null;
      this.publishKind(kind).catch((error) => this.error(`Publish ${kind} failed`, error));
    }, delay);
  }

  async publishKind(kind) {
    this.lastPublishedAt[kind] = Date.now();
    if (kind === 'health') {
      await this.publishHealth();
      return;
    }
    if (kind === 'diagnostics') {
      await this.publishDiagnostics();
      return;
    }
    if (kind === 'metadata') {
      await this.publishMetadata();
    }
  }

  async publishHealth() {
    await this.publishJson(this.topicFor('health'), {
      serial: this.evse.serial,
      endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      loggedIn: this.loggedIn,
      effectiveLoggedIn: this.effectiveLoggedIn(),
      mqttConnected: this.mqttConnected,
      authFailureReason: this.authFailureReason,
      lastSeen: this.lastSeen,
      lastStatusAt: this.lastStatusAt,
      updatedAt: new Date().toISOString(),
    });
  }

  async publishDiagnostics() {
    await this.publishJson(this.topicFor('diagnostics'), this.getDiagnostics());
  }

  async publishMetadata() {
    await this.publishJson(this.topicFor('metadata'), {
      serial: this.evse.serial,
      endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      brand: this.evse.brand,
      model: this.evse.model,
      hardwareVersion: this.evse.hardwareVersion,
      maxPower: this.evse.maxPower,
      maxElectricity: this.evse.maxElectricity,
      hotline: this.evse.hotline,
      updatedAt: new Date().toISOString(),
    });
  }

  async publishStatus() {
    if (!this.lastStatus) {
      return;
    }
    await this.publishJson(this.topicFor('status'), {
      serial: this.evse.serial,
      endpoint: this.evse.ip && this.evse.port ? `${this.evse.ip}:${this.evse.port}` : null,
      receivedAt: this.lastStatusAt,
      summary: {
        evseState: this.deriveEvseState(this.lastStatus),
        pluggedIn: this.isPluggedIn(this.lastStatus),
        charging: this.effectiveChargingState(this.lastStatus),
        hasError: Boolean(this.lastStatus.errorBits),
      },
      status: this.lastStatus,
    });
  }
}

class EVSEProxyApp extends Homey.App {
  async onInit() {
    this.proxy = new EVSEProxySession(this.homey);
    await this.proxy.start();
    this.log('EVSE Master Proxy MQTT app initialized');
  }

  async onUninit() {
    await this.proxy.stop();
  }

  async getHealth() {
    const diagnostics = this.proxy.getDiagnostics();
    return {
      ok: Boolean(diagnostics.config.serial),
      loggedIn: diagnostics.loggedIn,
      mqttConnected: diagnostics.mqttConnected,
      endpoint: diagnostics.endpoint,
      authFailureReason: diagnostics.authFailureReason,
      lastSeen: diagnostics.lastSeen,
      lastStatusAt: diagnostics.lastStatusAt,
    };
  }

  async getConfig() {
    return this.proxy.getConfig();
  }

  async setConfig(config) {
    return this.proxy.updateConfig(config || {});
  }

  async connectProxy(config) {
    if (config && Object.keys(config).length > 0) {
      await this.proxy.updateConfig(config);
    }
    return this.proxy.connect();
  }

  async getStatus() {
    return this.proxy.getStatus();
  }

  async refreshStatus() {
    return this.proxy.refreshStatus();
  }

  async getDiagnostics() {
    return this.proxy.getDiagnostics();
  }
}

module.exports = EVSEProxyApp;
