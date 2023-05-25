import inspect
import sys
import yaml

OPENAPI_DOC_BEGIN = "OpenAPI BEGIN"
OPENAPI_DOC_END = "OpenAPI END"

BASE_DOC = {
    "openapi": "3.0.3",
    "info": {
        "version": "2.0.0",
        "title": "Ganeti RAPI (Remote API)",
        "contact": {
            "email": "ganeti@googlegroups.com",
            "url": "https://ganeti.org/community.html",
        },
        "license": {
            "name": "BSD-2-Clause license",
        },
        "x-logo": {
            "url": "https://ganeti.org/static/Ganeti_logo.png"
        },
    },
    "externalDocs": {
        "description": "Ganeti Documentation",
        "url": "https://docs.ganeti.org",
    },
    "components": {
        "securitySchemes": {
            "basicAuth": {
                "type": "http",
                "scheme": "basic"
            },
        },
    },
    "security": [
        {
            "basicAuth": []
        },
    ],

    "tags": [
        {
            "name": "RAPI",
            "description": "API Information"
        },
        {
            "name": "Cluster",
            "description": "Manage Cluster"
        },
        {
            "name": "Nodes",
            "description": "Manage Nodes"
        },
        {
            "name": "Instances",
            "description": "Manage Instances"
        },
        {
            "name": "Groups",
            "description": "Manage Node Groups"
        },
        {
            "name": "Networks",
            "description": "Manage Networks"
        },
        {
            "name": "Operating Systems",
            "description": "OS Provider"
        },
    ],
    "paths": {}
}

def read_comments_from_file(file_path):
    with open(file_path, 'r') as file:
        code = compile(file.read(), file_path, 'exec')
    
    module = type(sys)(file_path)
    module.__file__ = file_path
    
    try:
        exec(code, module.__dict__)
    except Exception as e:
        print(f"Fehler beim Ausführen der Datei '{file_path}': {e}")
    paths = []
    
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and obj.__name__.startswith("R_"):
            comments = inspect.getdoc(obj)
            if comments:
                start_pos = comments.find(OPENAPI_DOC_BEGIN)
                end_pos = comments.find(OPENAPI_DOC_END)
                if start_pos >= 0 and end_pos >= 0:
                    openapi_comment = comments[start_pos + len(OPENAPI_DOC_BEGIN):end_pos]
                    try:
                        openapi_data = yaml.load(openapi_comment, Loader=yaml.SafeLoader)
                        BASE_DOC["paths"].update(openapi_data)
                    except yaml.YAMLError as e:
                        print("Warning: Failed to parse OpenAPI YAML data in class {}".format(name), file=sys.stderr)
                else:
                    print("Warning: Class {} does not yet contain OpenAPI data".format(name), file=sys.stderr)
    
    print(yaml.dump(BASE_DOC, sort_keys=False))


# Dateipfad zur 'rlib2.py' angeben
file_path = 'lib/rapi/rlib2.py'

# Kommentare aus der Datei einlesen
read_comments_from_file(file_path)