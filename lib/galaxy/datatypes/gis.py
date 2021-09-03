"""
GIS classes
"""

import nice_size
import rasterio

from galaxy.datatypes.binary import Binary


class Shapefile(Binary):
    """ The Shapefile data format:
            For more information please see http://en.wikipedia.org/wiki/Shapefile
    """

    composite_type = 'auto_primary_file'
    file_ext = "shp"

    def __init__(self, **kwd):
        super().__init__(**kwd)
        self.add_composite_file('shapefile.shp', description='Geometry File (shp)', is_binary=True, optional=False)
        self.add_composite_file('shapefile.shx', description='Geometry index File (shx)', is_binary=True, optional=False)
        self.add_composite_file('shapefile.dbf', description='Columnar attributes for each shape (dbf)', is_binary=True, optional=False)
        # optional
        self.add_composite_file('shapefile.prj', description='Projection description (prj)', is_binary=False, optional=True)
        self.add_composite_file('shapefile.sbn', description='Spatial index of the features (sbn)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.sbx', description='Spatial index of the features (sbx)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.fbn', description='Read only spatial index of the features (fbn)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.fbx', description='Read only spatial index of the features (fbx)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.ain', description='Attribute index of the active fields in a table (ain)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.aih', description='Attribute index of the active fields in a table (aih)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.atx', description='Attribute index for the dbf file (atx)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.ixs', description='Geocoding index (ixs)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.mxs', description='Geocoding index in ODB format (mxs)', is_binary=True, optional=True)
        self.add_composite_file('shapefile.shp.xml', description='Geospatial metadata in XML format (xml)', is_binary=False, optional=True)

    def generate_primary_file(self, dataset=None):
        rval = ['<html><head><title>Shapefile Galaxy Composite Dataset</title></head><p/>']
        rval.append('<div>This composite dataset is composed of the following files:<p/><ul>')
        for composite_name, composite_file in self.get_composite_files(dataset=dataset).items():
            fn = composite_name
            opt_text = ''
            if composite_file.optional:
                opt_text = ' (optional)'
            if composite_file.get('description'):
                rval.append(f"<li><a href=\"{fn}\" type=\"application/binary\">{fn} ({composite_file.get('description')})</a>{opt_text}</li>")
            else:
                rval.append(f'<li><a href="{fn}" type="application/binary">{fn}</a>{opt_text}</li>')
        rval.append('</ul></div></html>\n')
        return "\n".join(rval)

    def set_peek(self, dataset, is_multi_byte=False):
        """Set the peek and blurb text."""
        if not dataset.dataset.purged:
            dataset.peek = "Shapefile data"
            dataset.blurb = "Shapefile data"
        else:
            dataset.peek = "file does not exist"
            dataset.blurb = "file purged from disk"

    def display_peek(self, dataset):
        """Create HTML content, used for displaying peek."""
        try:
            return dataset.peek
        except Exception:
            return "Shapefile data"


class GeoTiff(Image):
    """ The GeoTiff data format:
            For more information please see http://en.wikipedia.org/wiki/GeoTIFF
    GeoTiff image format
    >>> from galaxy.datatypes.sniff import get_test_fname
    >>> fname = get_test_fname('test.geo.tif')
    >>> GeoTiff().sniff(fname)
    True
    >>> fname = get_test_fname('interval.interval')
    >>> GeoTiff().sniff(fname)
    False
    """
    file_ext = "geo.tiff"
    edam_format = "format_webprotege_012"
    edam_data = "data_webprotege_001"

    def __init__(self, **kwd):
        super().__init__(**kwd)

    def sniff(self, filename):
    	# A GeoTiff file contains a coordinate reference system (CRS) that is identified by an EPSG code.
        try:
            filedata = rasterio.open(filename)
            return (filedata.meta['driver'] == 'GTiff') and (filedata.meta['crs'] is not None)
        except Exception:
            return False

    def set_peek(self, dataset, is_multi_byte=False):
        if not dataset.dataset.purged:
            dataset.peek = "Image GeoTiff file"
            dataset.blurb = nice_size(dataset.get_size())
        else:
            dataset.peek = 'file does not exist'
            dataset.blurb = 'file purged from disk'

    def display_peek(self, dataset):
        try:
            return dataset.peek
        except Exception:
            return "Image GeoTiff file (%s)" % (nice_size(dataset.get_size()))
