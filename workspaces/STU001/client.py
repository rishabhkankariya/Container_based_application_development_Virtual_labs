from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

MICROSERVICE_URL = "http://sum-microservice:5000/add"


@app.route('/calc', methods=['GET'])
def calc():
    a = request.args.get('a', 10)
    b = request.args.get('b', 20)

    res = requests.get(f"{MICROSERVICE_URL}?a={a}&b={b}")

    return jsonify({
        "client_status": "success",
        "network_result": res.json()
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)