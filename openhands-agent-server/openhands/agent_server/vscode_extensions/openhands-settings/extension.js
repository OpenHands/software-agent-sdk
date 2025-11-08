// OpenHands Settings extension - minimal CommonJS JS
const vscode = require('vscode');

function activate(context) {
  const config = vscode.workspace.getConfiguration();
  const target = vscode.ConfigurationTarget.Global;

  config.update('workbench.colorTheme', 'Default Dark+', target);
  config.update('editor.fontSize', 14, target);
  config.update('editor.tabSize', 4, target);
  config.update('files.autoSave', 'afterDelay', target);
  config.update('files.autoSaveDelay', 1000, target);
  config.update('update.mode', 'none', target);
  config.update('telemetry.telemetryLevel', 'off', target);
  config.update('extensions.autoCheckUpdates', false, target);
  config.update('extensions.autoUpdate', false, target);
  config.update('chat.commandCenter.enabled', false, target);
}

function deactivate() {}

module.exports = { activate, deactivate };
