module.exports = {
  async getHealth({ homey }) {
    return homey.app.getHealth();
  },

  async getConfig({ homey }) {
    return homey.app.getConfig();
  },

  async setConfig({ homey, body }) {
    return homey.app.setConfig(body);
  },

  async connectProxy({ homey, body }) {
    return homey.app.connectProxy(body);
  },

  async getStatus({ homey }) {
    return homey.app.getStatus();
  },

  async refreshStatus({ homey }) {
    return homey.app.refreshStatus();
  },

  async getDiagnostics({ homey }) {
    return homey.app.getDiagnostics();
  },
};
