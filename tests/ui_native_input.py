"""Test-only CDP input driver: trusted mouse/keyboard events in the actual WebView2."""
import json
import threading
import time
import urllib.request

import websocket


def drive_input(port, process):
    def run():
        connection = None
        try:
            while process.poll() is None and connection is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=1) as response:
                        pages = json.load(response)
                    page = next(p for p in pages if p.get('type') == 'page' and 'index.html' in p.get('url', ''))
                    connection = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=3, suppress_origin=True)
                except Exception:
                    time.sleep(.1)
            if connection is None:
                return
            sequence = 0

            def call(method, params):
                nonlocal sequence
                sequence += 1
                connection.send(json.dumps({'id': sequence, 'method': method, 'params': params}))
                while True:
                    result = json.loads(connection.recv())
                    if result.get('id') == sequence:
                        if 'error' in result:
                            raise RuntimeError(result['error'])
                        return result.get('result', {})

            def evaluate(expression):
                return call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}).get('result', {}).get('value')

            evaluate('window.__uiNativeReady = true')
            while process.poll() is None:
                request = evaluate('window.__DONE ? {finished: true} : window.__uiNativeRequest || null')
                if request and request.get('finished'):
                    break
                if request:
                    if request['kind'] in ('click', 'move'):
                        point = {'x': request['x'], 'y': request['y']}
                        call('Input.dispatchMouseEvent', dict(type='mouseMoved', **point))
                        if request['kind'] == 'click':
                            call('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', clickCount=1, **point))
                            call('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', clickCount=1, **point))
                    else:
                        key = request['key']
                        params = {'key': key, 'code': 'Key' + key.upper(), 'windowsVirtualKeyCode': ord(key.upper()), 'modifiers': 2}
                        call('Input.dispatchKeyEvent', dict(type='keyDown', **params))
                        call('Input.dispatchKeyEvent', dict(type='keyUp', **params))
                    evaluate('window.__uiNativeRequest = null; window.__uiNativeDone = ' + str(request['id']))
                time.sleep(.02)
        except Exception as error:
            time.sleep(.5)  # The WebView closes its socket just before the app exits.
            if process.poll() is None:
                print('WebView input driver failed:', error, flush=True)
        finally:
            if connection:
                connection.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
