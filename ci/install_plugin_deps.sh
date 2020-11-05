#!/bin/bash
for file in $(find . -name "requirements.txt"); do
  python3 -m pip install -r $file
done
