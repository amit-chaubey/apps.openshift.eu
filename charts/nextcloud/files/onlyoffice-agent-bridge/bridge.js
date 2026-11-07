// Agent bridge: a system plugin that only answers calls made inside its own frame, by the
// headless browser of mcp-onlyoffice-live. In a person's editor it loads and stays idle.
(function (window) {
  "use strict";
  var queue = Promise.resolve();

  function serial(task) {
    var next = queue.then(task, task);
    queue = next.catch(function () {});
    return next;
  }

  window.Asc.plugin.init = function () {
    window.agentBridge.ready = true;
  };
  window.Asc.plugin.button = function () {};

  window.agentBridge = {
    ready: false,
    version: "1.0.0",
    // source: a function expression; scope: JSON data the function reads as Asc.scope
    command: function (source, scope, recalculate) {
      return serial(function () {
        return new Promise(function (resolve) {
          window.Asc.scope = scope || {};
          window.Asc.plugin.callCommand(source, false, recalculate !== false, function (value) {
            resolve(value === undefined ? null : value);
          });
        });
      });
    },
    method: function (name, args) {
      return serial(function () {
        return new Promise(function (resolve) {
          window.Asc.plugin.executeMethod(name, args || [], function (value) {
            resolve(value === undefined ? null : value);
          });
        });
      });
    },
    editorType: function () {
      return (window.Asc.plugin.info && window.Asc.plugin.info.editorType) || "";
    }
  };
})(window);
