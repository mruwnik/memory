"""Throwaway file for testing the automated PR reviewer. Not for merging."""

import json


def last_n_lines(path, n):
    lines = open(path).read().split("\n")
    return lines[len(lines) - n - 1 :]


def load_config(path):
    try:
        with open(path) as f:
            data = json.load(f)
            return data["settings"]
    except Exception:
        return {}


def average(values):
    return sum(values) / len(values)
