# source.py
import argparse  # helps parse commandline arguments
import json
import sys
import os
import requests
import datetime


def read_json(filepath):
  with open(filepath, "r") as f:
    return json.loads(f.read())


def log(message):
  log_json = {"type": "LOG", "log": message}
  print(json.dumps(log_json))


def spec():
  # Read the file named spec.json from the module directory as a JSON file
  current_script_directory = os.path.dirname(os.path.realpath(__file__))
  spec_path = os.path.join(current_script_directory, "spec.json")
  specification = read_json(spec_path)

  # form an Airbyte Message containing the spec and print it to stdout
  airbyte_message = {"type": "SPEC", "spec": specification}
  # json.dumps converts the JSON (python dict) to a string
  print(json.dumps(airbyte_message))


def run(args):
  parent_parser = argparse.ArgumentParser(add_help=False)
  main_parser = argparse.ArgumentParser()
  subparsers = main_parser.add_subparsers(title="commands", dest="command")

  # Accept the spec command
  subparsers.add_parser("spec", help="outputs the json configuration specification", parents=[parent_parser])

  # Accept the check command
  check_parser = subparsers.add_parser("check", help="checks the config used to connect", parents=[parent_parser])
  required_check_parser = check_parser.add_argument_group("required named arguments")
  required_check_parser.add_argument("--config", type=str, required=True, help="path to the json configuration file")

  # Accept the discover command
  discover_parser = subparsers.add_parser("discover", help="outputs a catalog describing the source's schema", parents=[parent_parser])
  required_discover_parser = discover_parser.add_argument_group("required named arguments")
  required_discover_parser.add_argument("--config", type=str, required=True, help="path to the json configuration file")

  # Accept the read command
  read_parser = subparsers.add_parser("read", help="reads the source and outputs messages to STDOUT", parents=[parent_parser])
  read_parser.add_argument("--state", type=str, required=False, help="path to the json-encoded state file")
  required_read_parser = read_parser.add_argument_group("required named arguments")
  required_read_parser.add_argument("--config", type=str, required=True, help="path to the json configuration file")
  required_read_parser.add_argument(
      "--catalog", type=str, required=True, help="path to the catalog used to determine which data to read"
  )

  parsed_args = main_parser.parse_args(args)
  command = parsed_args.command

  if command == "spec":
    spec()
  elif command == "check":
    config_file_path = get_input_file_path(parsed_args.config)
    config = read_json(config_file_path)
    check(config)
  elif command == "discover":
    discover()
  elif command == "read":
    config = read_json(get_input_file_path(parsed_args.config))
    configured_catalog = read_json(get_input_file_path(parsed_args.catalog))
    read(config, configured_catalog)
  else:
    # If we don't recognize the command log the problem and exit with an error code greater than 0 to indicate the process
    # had a failure
    log("Invalid command. Allowable commands: [spec, check]")
    sys.exit(1)

  # A zero exit code means the process successfully completed
  sys.exit(0)

def discover():
  catalog = {
    "streams": [{
      "name": "stock_prices",
      "supported_sync_modes": ["full_refresh"],
      "json_schema": {
        "properties": {
          "date": {
            "type": "string"
          },
          "price": {
            "type": "number"
          },
          "stock_ticker": {
            "type": "string"
          }
        }
      }
    }]
  }
  airbyte_message = {"type": "CATALOG", "catalog": catalog}
  print(json.dumps(airbyte_message))

def read(config, catalog):
  # Find the stock_prices stream if it is present in the input catalog
  stock_prices_stream = None
  for configured_stream in catalog["streams"]:
    if configured_stream["stream"]["name"] == "stock_prices":
      stock_prices_stream = configured_stream

  if stock_prices_stream is None:
    log("No streams selected")
    return

  # We only support full_refresh at the moment, so verify the user didn't ask for another sync mode
  if stock_prices_stream["sync_mode"] != "full_refresh":
    log("This connector only supports full refresh syncs! (for now)")
    sys.exit(1)

  # If we've made it this far, all the configuration is good and we can pull the last 7 days of market data
  api_key = config["api_key"]
  stock_ticker = config["stock_ticker"]
  response = _call_api(f"/stock/{stock_ticker}/chart/7d", api_key)
  if response.status_code != 200:
    # In a real scenario we'd handle this error better :)
    log("Failure occurred when calling IEX API")
    sys.exit(1)
  else:
    # Sort the stock prices ascending by date then output them one by one as AirbyteMessages
    prices = sorted(response.json(), key=lambda record: datetime.datetime.strptime(record["date"], '%Y-%m-%d'))
    for price in prices:
      data = {"date": price["date"], "stock_ticker": price["symbol"], "price": price["close"]}
      # emitted_at is in milliseconds so we multiply by 1000
      record = {"stream": "stock_prices", "data": data, "emitted_at": int(datetime.datetime.now().timestamp()) * 1000}
      output_message = {"type": "RECORD", "record": record}
      print(json.dumps(output_message))


def _call_api(endpoint, token):
  return requests.get("https://cloud.iexapis.com/v1/" + endpoint + "?token=" + token)


def check(config):
  # Validate input configuration by attempting to get the price of the input stock ticker for the previous day
  response = _call_api(endpoint="stock/" + config["stock_ticker"] + "/previous", token=config["api_key"])
  if response.status_code == 200:
    result = {"status": "SUCCEEDED"}
  elif response.status_code == 403:
    # HTTP code 403 means authorization failed so the API key is incorrect
    result = {"status": "FAILED", "message": "API Key is incorrect."}
  else:
    result = {"status": "FAILED", "message": "Input configuration is incorrect. Please verify the input stock ticker and API key."}

  output_message = {"type": "CONNECTION_STATUS", "connectionStatus": result}
  print(json.dumps(output_message))


def get_input_file_path(path):
  if os.path.isabs(path):
    return path
  else:
    return os.path.join(os.getcwd(), path)

def main():
  arguments = sys.argv[1:]
  run(arguments)


if __name__ == "__main__":
  main()