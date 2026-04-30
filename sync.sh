rsync -rzuP \
	--exclude .git \
	--exclude .venv \
	--exclude __pycache__ \
	--exclude node_modules \
	--exclude static \
	lemon:ldq/quant-arena/. .
