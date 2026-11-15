"""`python -m afd` starts the app on the port Databricks Apps assigns.

Databricks Apps sets DATABRICKS_APP_PORT; locally it defaults to 8765.
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("afd.main:app", host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8765")))
