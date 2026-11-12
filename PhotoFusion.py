#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lance LLF PhotoFusion (interface dans une fenêtre d'application)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == '__main__':
    from photofusion.server import main
    main()
