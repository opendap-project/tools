"""odw: the OpenDAP maintainers' workspace tools.

One package across the tools root's domain folders (HL-71): this folder holds the shared core, and each domain's
`odw/` folder adds its modules. In a checkout every such folder is on the path (the editable install), and
extend_path joins them; an installed wheel has them in one folder.
"""
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
