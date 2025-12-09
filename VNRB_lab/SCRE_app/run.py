from threading import Thread
import app as sec_app
import proxy

def start_backend():
    sec_app.app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False
    )

def start_reverse_proxy():
    proxy.start_proxy()

if __name__ == "__main__":
    t1 = Thread(target=start_backend)
    t2 = Thread(target=start_reverse_proxy)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
