# -*- coding: utf-8 -*-
import re
import time
import ast
import StringIO
from pymongo import ASCENDING, DESCENDING
import lmfdb.base
from lmfdb.base import app
from lmfdb.utils import to_dict
from lmfdb.abvar.fq import abvarfq_page
from lmfdb.search_parsing import parse_ints, parse_newton_polygon, parse_list_start, parse_abvar_decomp, parse_count, parse_start
from isog_class import validate_label, AbvarFq_isoclass
from stats import AbvarFqStats
from flask import flash, render_template, url_for, request, redirect, make_response, send_file
from markupsafe import Markup
from sage.misc.cachefunc import cached_function
from sage.rings.all import PolynomialRing

#########################
#   Database connection
#########################

@cached_function
def db():
    return lmfdb.base.getDBConnection().abvar.fq_isog

#########################
#    Top level
#########################

def get_bread(*breads):
    bc = [('Abelian Varieties', url_for(".abelian_varieties")),
          ('Fq', url_for(".abelian_varieties"))]
    map(bc.append, breads)
    return bc

abvarfq_credit = 'Kiran Kedlaya'

@app.route("/EllipticCurves/Fq")
def ECFq_redirect():
    return redirect(url_for("abvarfq.abelian_varieties"), **request.args)

def learnmore_list():
    return [('Completeness of the data', url_for(".completeness_page")),
            ('Source of the data', url_for(".how_computed_page")),
            ('Labels for isogeny classes of abelian varieties', url_for(".labels_page"))]

# Return the learnmore list with the matchstring entry removed
def learnmore_list_remove(matchstring):
    return filter(lambda t:t[0].find(matchstring) < 0, learnmore_list())

#########################
#  Search/navigate
#########################

@abvarfq_page.route("/")
def abelian_varieties():
    args = request.args
    if args:
        return abelian_variety_search(**args)
    else:
        info = {}
        stats = AbvarFqStats()
        info['col_heads'] = sorted(int(b) for b in stats.counts['qs'])[:23] # q < 50
        info['row_heads'] = stats.counts['gs']
        info['table'] = stats.counts['qg_count']
        return render_template("abvarfq-index.html", title="Isogeny Classes of Abelian Varieties over Finite Fields",
                               info=info, credit=abvarfq_credit, bread=get_bread(), learnmore=learnmore_list())

@abvarfq_page.route("/<int:g>/")
def abelian_varieties_by_g(g):
    D = to_dict(request.args)
    if 'g' not in D: D['g'] = g
    return abelian_variety_search(**D)

@abvarfq_page.route("/<int:g>/<int:q>/")
def abelian_varieties_by_gq(g, q):
    D = to_dict(request.args)
    if 'g' not in D: D['g'] = g
    if 'q' not in D: D['q'] = q
    return abelian_variety_search(**D)

@abvarfq_page.route("/<int:g>/<int:q>/<iso>")
def abelian_varieties_by_gqi(g, q, iso):
    label = abvar_label(g,q,iso)
    try:
        validate_label(label)
    except ValueError as err:
        flash(Markup("Error: <span style='color:black'>%s</span> is not a valid label: %s." % (label, str(err))), "error")
        return search_input_error()
    try:
        cl = AbvarFq_isoclass.by_label(label)
    except ValueError:
        flash(Markup("Error: <span style='color:black'>%s</span> is not in the database." % (label)), "error")
        return search_input_error()
    return render_template("show-abvarfq.html",
                           credit=abvarfq_credit,
                           title='Abelian Variety isogeny class %s over $%s$'%(label, cl.field()),
                           bread=get_bread(('Search Results', '.')),
                           cl=cl,
                           learnmore=learnmore_list())


def abelian_variety_search(**args):
    info = to_dict(args)

    if 'download' in info and info['download'] != 0:
        return download_search(info)

    bread = get_bread(('Search Results', '.'))
    if 'jump' in info:
        return by_label(info.get('label',''))
    query = {}

    try:
        parse_ints(info,query,'q')
        parse_ints(info,query,'g')
        if 'simple_only' in info and info['simple_only'] == 'yes':
            query['decomposition'] = {'$size' : 1}
        if 'primitive_only' in info and info['primitive_only'] == 'yes':
            query['primitive_models'] = []
        parse_ints(info,query,'p_rank')
        parse_newton_polygon(info,query,'newton_polygon',qfield='slopes')
        parse_list_start(info,query,'initial_coefficients',qfield='polynomial',index_shift=1)
        parse_list_start(info,query,'abvar_point_count',qfield='A_counts')
        parse_list_start(info,query,'curve_point_count',qfield='C_counts')
        parse_abvar_decomp(info,query,'decomposition')
    except ValueError:
        return search_input_error(info, bread)

    info['query'] = query
    count = parse_count(info, 50)
    start = parse_start(info)

    cursor = db().find(query)
    nres = cursor.count()
    if start >= nres:
        start -= (1 + (start - nres) / count) * count
    if start < 0:
        start = 0

    #res = cursor.sort([]).skip(start).limit(count)
    res = cursor.skip(start).limit(count)
    res = list(res)
    info['abvars'] = [AbvarFq_isoclass(x) for x in res]
    info['number'] = nres
    info['start'] = start
    info['count'] = count
    info['more'] = int(start + count < nres)
    if nres == 1:
        info['report'] = 'unique_match'
    elif nres > count or start != 0:
        info['report'] = 'displaying matches %s-%s of %s' %(start + 1, min(nres, start+count), nres)
    else:
        info['report'] = 'displaying all %s matches' % nres
    t = 'Abelian Variety search results'
    return render_template("abvarfq-search-results.html", info=info, credit=abvarfq_credit, bread=bread, title=t)

def search_input_error(info=None, bread=None):
    if info is None: info = {'err':'','query':{}}
    if bread is None: bread = get_bread(('Search Results', '.'))
    return render_template("abvarfq-search-results.html", info=info, title='Abelian Variety search input error', bread=bread)

@abvarfq_page.route("/<label>")
def by_label(label):
    label = label.replace(" ", "")
    try:
        validate_label(label)
    except ValueError as err:
        flash(Markup("Error: <span style='color:black'>%s</span> is not a valid label: %s." % (label, str(err))), "error")
        return search_input_error()
    g, q, iso = split_label(label)
    return redirect(url_for(".abelian_varieties_by_gqi", g = g, q = q, iso = iso))

def download_search(info):
    dltype = info['Submit']
    R = PolynomialRing(ZZ, 'x')
    delim = 'bracket'
    com = r'\\'  # single line comment start
    com1 = ''  # multiline comment start
    com2 = ''  # multiline comment end
    filename = 'weil_polynomials.gp'
    mydate = time.strftime("%d %B %Y")
    if dltype == 'sage':
        com = '#'
        filename = 'weil_polynomials.sage'
    if dltype == 'magma':
        com = ''
        com1 = '/*'
        com2 = '*/'
        delim = 'magma'
        filename = 'weil_polynomials.m'
    s = com1 + "\n"
    s += com + " Weil polynomials downloaded from the LMFDB on %s.\n"%(mydate)
    s += com + " Below is a list (called data), collecting the weight 1 L-polynomial\n"
    s += com + " attached to each isogeny class of an abelian variety.\n"
    s += "\n" + com2
    s += "\n"

    if dltype == 'magma':
        s += 'P<x> := PolynomialRing(Integers()); \n'
        s += 'data := ['
    else:
        if dltype == 'sage':
            s += 's = polygen(ZZ) \n'
        s += 'data = [ '
    s += '\\\n'
    res = db().find(ast.literal_eval(info["query"]))
    for f in res:
        poly = R([int(c) for c in f['polynomial']])
        s += str(poly) + ',\\\n'
    s = s[:-3]
    s += ']\n'
    if delim == 'magma':
        s = s.replace('[', '[*')
        s = s.replace(']', '*]')
        s += ';'
    strIO = StringIO.StringIO()
    strIO.write(s)
    strIO.seek(0)
    return send_file(strIO,
                     attachment_filename=filename,
                     as_attachment=True)

@abvarfq_page.route("/Completeness")
def completeness_page():
    t = 'Completeness of the Weil polynomial data'
    bread = _common_bread + [('Completeness', '')]
    credit = 'Kiran Kedlaya'
    return render_template("single.html", kid='dq.abvar.extent',
                           credit=credit, title=t, bread=bread, learnmore=learnmore_list_remove('Completeness'))

@abvarfq_page.route("/Source")
def how_computed_page():
    pass

@abvarfq_page.route("/Labels")
def labels_page():
    pass

def decomposition_display(current_class, factors):
    if len(factors) == 1 and factors[0][1] == 1:
        return 'simple'
    ans = ''
    for factor in factors:
        if ans != '':
            ans += '$\times$ '
        ans += factor_display_knowl(factor[0]) + '<sup>factor[1]</sup> '
    return ans

    
def factor_display_knowl(label):
    return '<a title = " [abvar.decomposition.data]" knowl="abvar.decomposition.data" kwargs="label=' + str(label) + '">' + label + '</a>'
    
def decomposition_data(label):
    C = base.getDBConnection()
    return decomposition_knowl_guts(label,C)
    
def decomposition_knowl_guts(label,C):
    abvar = C.abvarfq.find_one({ 'label' : label })
    inf = '<div><h4>Dimension:</h4> ' + str(abvar['g']) + '/n'
    inf += '<h4>Number field:</h4> ' + str(abvar['number_field']) + '/n'
    inf += '<h4>Galois group:</h4> ' + str(abvar['galois_group']) + '/n'
    inf += '<h4>$p$-rank</h4> ' + str(abvar['p_rank']) + '</div>'
    inf += '<div align="right">'
    inf += '<a href="/abvar/fq/%s">%s home page</a>' % (label, label)
    inf += '</div>'
              
lmfdb_label_regex = re.compile(r'(\d+)\.(\d+)\.([a-z_]+)')
                                  
def split_label(lab):
    return lmfdb_label_regex.match(lab).groups()
    
def abvar_label(g, q, iso):
    return "%s.%s.%s" % (g, q, iso)
    

                
            
    
