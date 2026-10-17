/*
 * Tiles the signed-in user name and the time (UTC, refreshed every minute)
 * over the page. The client address is not known to the browser, so it is
 * left out.
 */
angular.module('guacWatermark', []);
angular.module('index').requires.push('guacWatermark');

angular.module('guacWatermark').run(['$injector', function guacWatermark($injector) {

    var $rootScope = $injector.get('$rootScope');
    // a renamed service leaves the overlay off, never breaks the bootstrap
    var authenticationService;
    try {
        authenticationService = $injector.get('authenticationService');
    }
    catch (e) {
        return;
    }

    var layer = document.createElement('div');
    layer.className = 'guac-watermark';

    var escapeXml = function escapeXml(text) {
        return String(text).replace(/[&<>"']/g, function (c) {
            return '&#' + c.charCodeAt(0) + ';';
        });
    };

    var pad = function pad(n) {
        return (n < 10 ? '0' : '') + n;
    };

    var now = function now() {
        var d = new Date();
        return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate())
            + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ' UTC';
    };

    var tile = function tile(lines) {
        var text = '';
        for (var i = 0; i < lines.length; i++)
            text += '<tspan x="20" dy="' + (i ? 22 : 0) + '">' + escapeXml(lines[i]) + '</tspan>';
        var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="240">'
            + '<text y="140" transform="rotate(-25 210 120)" font-family="sans-serif"'
            + ' font-size="18" fill="#000" stroke="#fff" stroke-width="3"'
            + ' paint-order="stroke">' + text + '</text></svg>';
        return 'url("data:image/svg+xml,' + encodeURIComponent(svg) + '")';
    };

    var render = function render() {
        if (!layer.parentNode && document.body)
            document.body.appendChild(layer);
        var username = authenticationService.getCurrentUsername();
        if (!username) {
            layer.classList.remove('guac-watermark-shown');
            return;
        }
        layer.style.backgroundImage = tile([username, now()]);
        layer.classList.add('guac-watermark-shown');
    };

    $rootScope.$on('guacLogin', function () { setTimeout(render, 0); });
    $rootScope.$on('guacLogout', function () { setTimeout(render, 0); });
    $rootScope.$on('$locationChangeSuccess', function () { setTimeout(render, 0); });
    setInterval(render, 60000);
    render();

}]);
