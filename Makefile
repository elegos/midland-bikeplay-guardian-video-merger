all: prerequisites convert.bin

prerequisites:
	pipenv install --dev

convert.bin: convert.py
	pipenv run nuitka --standalone --onefile --output-filename=convert.bin $<

clean:
	rm -f convert.bin
	rm -rf convert.build
	rm -rf convert.dist

.PHONY: all install-deps clean
