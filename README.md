## ifrs9pro_backend
...
### How to setup the server for development
- First install nix for your platform from https://nixos.org/download/
- Go to the base directory(same one this README is in) and run this command
```sh
nix-shell 
```
- Install the packages needed
```sh
poetry install
```
- Run the development server
```sh
poetry run uvicorn main:app --reload
```

