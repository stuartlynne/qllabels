
# vim: noexpandtab tabstop=8 shiftwidth=8

LARGE = "192.168.40.93"
#SMALL = "192.168.40.42"
SMALL = "192.168.40.93"


all:
	@echo "make sdist | install | uninstall | bdist"

.phony: all both frame1 body1 clean sdist install uninstall bdist

both: frame1 body1

frame1:
	./qllabels/qllabels.py --save-png  --save-raster --label 62x100 \
		tests/frame1/030489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf \
		< tests/frame1/030489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf 

frame1-62x100:
	./qllabels/qllabels.py --save-png  --save-raster --label 62x100 --hostname $(SMALL) \
		tests/frame1/030489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf \
		< tests/frame1/030489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf 

body1:
	./qllabels/qllabels.py --save-png  --save-raster --label 102x152  \
		tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf \
		< tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf 
	./qllabels/qllabels.py --save-png  --save-raster --label 103x164  \
		tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf \
		< tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf

body1-102x152:
	./qllabels/qllabels.py --save-png  --save-raster --label 102x152 --hostname $(LARGE)  \
		tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf \
		< tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf 
body1-103x164:
	./qllabels/qllabels.py --save-png  --save-raster --label 103x164  --hostname $(LARGE) \
		tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf \
		< tests/body1/686bbb13344a437292f0ca4b24ae1962_bib-733_port-8000_antenna-0_type-Body.pdf

clean:
	rm -f */*pyc
	rm -rf build dist *.egg-info

.PHONY: sdist install bdist


bdist:
	python3 setup.py $@
sdist:
	python3 setup.py $@
install:
	python3 setup.py $@

uninstall:
	pip3 uninstall qllabels
