from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/add', methods=['GET'])
def add_numbers():
    try:
        a = float(request.args.get('a'))
        b = float(request.args.get('b'))

        return jsonify({
            "number1": a,
            "number2": b,
            "sum": a + b
        })

    except (TypeError, ValueError):
        return jsonify({
            "error": "Please provide two valid numbers"
        }), 400


@app.route('/')
def home():
    return "Addition Microservice is Running"


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)