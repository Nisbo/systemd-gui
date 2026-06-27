from systemd_gui import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8850, debug=True)
