#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Feb 14 17:55:39 2020

@author:
Maximilian N. Günther
MIT Kavli Institute for Astrophysics and Space Research, 
Massachusetts Institute of Technology,
77 Massachusetts Avenue,
Cambridge, MA 02109, 
USA
Email: maxgue@mit.edu
Web: www.mnguenther.com
"""

from __future__ import print_function, division, absolute_import

#::: modules
import os, sys
import numpy as np
import matplotlib.pyplot as plt
import warnings
from pprint import pprint
from datetime import datetime
from astropy.stats import sigma_clip
from wotan import flatten, slide_clip
from transitleastsquares import transitleastsquares as tls
from transitleastsquares import transit_mask, cleaned_array, catalog_info
from ..exoworlds_rdx.lightcurves.index_transits import index_transits
import time as timer
import contextlib

#::: my modules
try:
    from exoworlds.tess import tessio
except:
    pass
from .fast_slide_clip import fast_slide_clip

#::: plotting settings
import seaborn as sns
sns.set(context='paper', style='ticks', palette='deep', font='sans-serif', font_scale=1.5, color_codes=True)
sns.set_style({"xtick.direction": "in","ytick.direction": "in"})
sns.set_context(rc={'lines.markeredgewidth': 1})

    

###############################################################################
#::: print to logfile
###############################################################################
def logprint(*text, options=None):
    original = sys.stdout
    try:
        with open(os.path.join(options['outdir'],'logfile.log'), 'a' ) as f:
            sys.stdout = f
            print(*text)
    except OSError:
        pass #For unknown reasons, the combination of open() and os.path.join() does not work on some Windows versions
    sys.stdout = original
    
    
    
###############################################################################
#::: pretty-print to logfile
###############################################################################
def logpprint(*text, options=None):
    original = sys.stdout
    try:
        with open(os.path.join(options['outdir'],'logfile.log'), 'a' ) as f:
            sys.stdout = f
            pprint(*text)
    except OSError:
        pass #For unknown reasons, the combination of open() and os.path.join() does not work on some Windows versions
    sys.stdout = original


    
###############################################################################
#::: apply a mask (if wished so)
###############################################################################
def mask(time, flux, flux_err, period, duration, T0):
    intransit = transit_mask(time, period, duration, T0)
    time = time[~intransit]
    flux = flux[~intransit]
    if flux_err is not None:
        flux_err = flux_err[~intransit]
        time, flux, flux_err = cleaned_array(time, flux, flux_err)
    else:
        time, flux = cleaned_array(time, flux)
    return time, flux, flux_err
    


###############################################################################
#::: apply a mask (if wished so)
###############################################################################
def is_multiple_of(a, b, tolerance=0.05):
    a = np.float(a)
    b = np.float(b) 
    result = a % b
    return (abs(result/b) <= tolerance) or (abs((b-result)/b) <= tolerance)



###############################################################################
#::: get TLS kwargs from TICv8
###############################################################################
def get_tls_kwargs_by_tic(tic_id, sigma=3, tls_kwargs=None, quiet=True):
    #mass comes first, radius comes second in the TLS source code for catalog_info()
    u, M_star, M_star_lerr, M_star_uerr, R_star, R_star_lerr, R_star_uerr = catalog_info(TIC_ID=int(tic_id))
    if not quiet:
        print('TICv8 info:')
        print('Quadratic limb darkening u_0, u_1', u[0], u[1])
        print('Stellar radius', R_star, '+', R_star_lerr, '-', R_star_uerr)
        print('Stellar mass', M_star, '+', M_star_lerr, '-', M_star_uerr)
    if tls_kwargs is None: tls_kwargs = {}
    tls_kwargs['R_star']=float(R_star)
    tls_kwargs['R_star_min']=R_star-sigma*R_star_lerr
    tls_kwargs['R_star_max']=R_star+sigma*R_star_uerr
    tls_kwargs['M_star']=float(M_star)
    tls_kwargs['M_star_min']=M_star-sigma*M_star_lerr
    tls_kwargs['M_star_max']=M_star+sigma*M_star_uerr
    tls_kwargs['u']=u    
    return tls_kwargs



###############################################################################
#::: TLS search on an input lightcurve
###############################################################################
def tls_search(time, flux, flux_err,
               known_transits=None,
               tls_kwargs=None,
               wotan_kwargs=None,
               options=None):
    '''
    Summary:
    -------
    This runs TLS on these data with the given infos
    
    Inputs:
    -------
    time : array of flaot
        time stamps of observations
    flux : array of flaot
        normalized flux
    flux_err : array of flaot
        error of normalized flux
        
    Optional Inputs:
    ----------------
    known_transits : None or dict
        >> can be used to mask known transits before running TLS
        if None
            nothing happens
        if dict 
            if one transit is already known, give for example: 
                known_transits = {'period':[1.3], 'duration':[2.1], 'epoch':[245800.0]}
            if multiple transits are already known, give for example: 
                known_transits = {'name':['b','c'], 'period':[1.3, 21.0], 'duration':[2.1, 4.1], 'epoch':[245800.0, 245801.0]}
            'period' is the period of the known transit(s)
            'duration' is the total duration of the known transit(s), i.e. from first ingress point to last egrees point, in days
            'epoch' is the epoch of the known transit(s)
        
    tls_kwargs : None, str or dict:
        >> can be used to fine-tune the TLS algorithm
        if None
            the default parameters will be chosen (see below)
        if 'default'
            the default parameters will be chosen (see below)
        if dict
            a dictionary with the following keywords is expected; 
            missing keywords will be replaced with default values
            R_star : float
                radius of the star (e.g. median)
                default 1 R_sun (from TLS)
            R_star_min : float
                minimum radius of the star (e.g. 1st percentile)
                default 0.13 R_sun (from TLS)
            R_star_max : float
                maximum radius of the star (e.g. 99th percentile)
                default 3.5 R_sun (from TLS)
            M_star : float
                mass of the star (e.g. median)
                default 1. M_sun (from TLS)
            M_star_min : float
                minimum mass of the star (e.g. 1st percentile)
                default 0.1 M_sun (from TLS)
            M_star_max : float
                maximum mass of the star (e.g. 99th percentile)
                default 1. M_sun (from TLS)    
            u : list
                quadratic limb darkening parameters
                default [0.4804, 0.1867]
            SNR_threshold : float
                the SNR threshold at which to stop the TLS search
                default 5
            SDE_threshold : float
                the SDE threshold at which to stop the TLS search
                default -inf
            FAP_threshold : float
                the False Alarm Probability threshold at which to stop the TLS search
                default inf
        
    wotan_kwargs : None, str, or dict:
        >> can be used to detrend the data before the TLS search
        if None
            the default detrending will run (see below)
        if str is 'default'
            the default detrending will run (see below)
        if str is 'off'
            no detrending will run
        if dict
            a dictionary with two sub-dictionaries is expected; 
            missing keywords will be replaced with default values
            wotan_kwargs['slide_clip'] : dict
                this dictionary contains all slide clipping arguments
                window_length : float
                    slide clip window length
                    default 1
                low : float
                    slide clip lower sigma
                    default 20
                high : float
                    slide clip upper sigma
                    default 3
            wotan_kwargs['flatten'] : dict
                this dictionary contains contains all detrending arguments
                method : str
                    detrending method
                    default 'biweight'
                window_length : float
                    detrending window length in days
                    default 1         
            
    options : None or dict, keywords:
        >> can be used for any general options
        if None
            the default options will be used (see below)
        if dict
            a dcitionary with the following keywords is expected;
            missing keywords will be replaced with default values
            show_plot : bool
                can show a plot of each phase-folded transit candidate and TLS model in the terminal 
                default is True
            save_plot : bool or str
                can save a plot of each phase-folded transit candidate and TLS model into outdir
                if True, will be set to '123'
                if str, then: '1': detrended plot, '2': TLS plot, '3': all TLS plots, and any combinations thereof
                default is True
            save_csv : bool
                can save a csv of the detrended lightcurve
                default is True
            outdir : string
                if None
                    a new directory called "results" will be created in the current folder
                default is "tls_results_[wotan_flatten_method]_[wotan_flatten_window_length]"
        
    Returns:
    -------
    List of all TLS results
    '''
    
    #::: seeed
    np.random.seed(42)
    
    
    #::: handle inputs
    if flux_err is None:
        ind = np.where( ~np.isnan(time*flux) )[0]
        time = time[ind]
        flux = flux[ind]
    else:
        ind = np.where( ~np.isnan(time*flux*flux_err) )[0]
        time = time[ind]
        flux = flux[ind]
        flux_err = flux_err[ind]
    
    time_input = 1.*time
    flux_input = 1.*flux #for plotting
        
    
    if type(wotan_kwargs)==str and wotan_kwargs=='off': 
        detrend = False
    else:
        detrend = True
        if (wotan_kwargs is None) or (type(wotan_kwargs)==str and wotan_kwargs=='default'): wotan_kwargs={} 
        if 'slide_clip' not in wotan_kwargs: wotan_kwargs['slide_clip'] = {}
        if wotan_kwargs['slide_clip'] is not None:
            if 'window_length' not in wotan_kwargs['slide_clip']: wotan_kwargs['slide_clip']['window_length'] = 1.
            if 'low' not in wotan_kwargs['slide_clip']: wotan_kwargs['slide_clip']['low'] = 20.
            if 'high' not in wotan_kwargs['slide_clip']: wotan_kwargs['slide_clip']['high'] = 3.
    
    if 'flatten' not in wotan_kwargs: wotan_kwargs['flatten'] = {}
    if wotan_kwargs['flatten'] is not None:
        if 'method' not in wotan_kwargs['flatten']: wotan_kwargs['flatten']['method'] = 'biweight'
        if 'window_length' not in wotan_kwargs['flatten']: wotan_kwargs['flatten']['window_length'] = 1.
    #the rest is filled automatically by Wotan
        
    if tls_kwargs is None: tls_kwargs = {}
    if 'show_progress_bar' not in tls_kwargs: tls_kwargs['show_progress_bar'] = False
    if 'SNR_threshold' not in tls_kwargs: tls_kwargs['SNR_threshold'] = 5.
    if 'SDE_threshold' not in tls_kwargs: tls_kwargs['SDE_threshold'] = -np.inf #don't trust SDE
    if 'FAP_threshold' not in tls_kwargs: tls_kwargs['FAP_threshold'] = np.inf #don't trust FAP 
    tls_kwargs_original = {key: tls_kwargs[key] for key in tls_kwargs.keys() if key not in ['SNR_threshold','SDE_threshold','FAP_threshold']} #for the original tls
    #the rest is filled automatically by TLS
    
    if options is None: options = {}
    if 'show_plot' not in options: options['show_plot'] = True
    if type(options['show_plot'])==bool and (options['show_plot'] is True): options['show_plot']='123' #1: detrended plot, 2: TLS plot, 3: all TLS plots
    if type(options['show_plot'])==bool and (options['show_plot'] is False): options['show_plot']='' #1: detrended plot, 2: TLS plot, 3: all TLS plots
    if 'save_plot' not in options: options['save_plot'] = True
    if type(options['save_plot'])==bool and (options['save_plot'] is True): options['save_plot']='123' #1: detrended plot, 2: TLS plot, 3: all TLS plots
    if type(options['save_plot'])==bool and (options['save_plot'] is False): options['save_plot']='' #1: detrended plot, 2: TLS plot, 3: all TLS plots
    if 'save_csv' not in options: options['save_csv'] = True
    if 'outdir' not in options: 
        if detrend:
            options['outdir'] = 'tls_results_'+wotan_kwargs['flatten']['method']+'_'+str(wotan_kwargs['flatten']['window_length'])
        else:
            options['outdir'] = 'tls_results_undetrended'
    if 'quiet' not in options: options['quiet'] = True
    if 'inj_period' not in options: options['inj_period'] = np.nan
    
    
    #::: init
    SNR = 1e12
    SDE = 1e12
    FAP = 0
    FOUND_SIGNAL = False
    results_all = []      
    if len(options['outdir'])>0 and not os.path.exists(options['outdir']): os.makedirs(options['outdir'])  
    
    
    #::: logprint
    with open( os.path.join(options['outdir'], 'logfile.log'), 'w' ) as f:
        f.write('TLS search, UTC ' + datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') + '\n')
    logprint('\nWotan kwargs:', options=options)
    logpprint(wotan_kwargs, options=options)
    logprint('\nTLS kwargs:', options=options)
    logpprint(tls_kwargs, options=options)
    logprint('\nOptions:', options=options)
    logpprint(options, options=options)
    
    # timer1 = timer.time()
    # print('t1', timer1 - timer0)
            
    #::: apply a mask (if wished so)
    if known_transits is not None:
        for period, duration, T0 in zip(known_transits['period'], known_transits['duration'], known_transits['epoch']):
            time, flux, flux_err = mask(time, flux, flux_err, period, duration, T0)
    
    
    #::: global sigma clipping
    flux = sigma_clip(flux, sigma_upper=3, sigma_lower=20)
    
    # timer2 = timer.time()
    # print('t2', timer2 - timer0)
    
    #::: detrend (if wished so)
    if detrend:
        
        #::: slide clipping (super slow)
        # if wotan_kwargs['slide_clip'] is not None: flux = slide_clip(time, flux, **wotan_kwargs['slide_clip']) #slide_clip is super slow (10 seconds for a TESS 2 min lightcurve for a single Sector)
        # timer3a = timer.time()
        # print('t3a', timer3a - timer0)   
    
        #::: fast slide clipping (super fast)
        if wotan_kwargs['slide_clip'] is not None: flux = fast_slide_clip(time, flux, **wotan_kwargs['slide_clip']) #slide_clip is super fast (<1 seconds for a TESS 2 min lightcurve for a single Sector)
        # timer3a = timer.time()
        # print('t3a', timer3a - timer0)   
        
        #::: detrending (super fast)
        if wotan_kwargs['flatten'] is not None: flux, trend = flatten(time, flux, return_trend=True, **wotan_kwargs['flatten']) #flatten is super fast, (<1 second for a TESS 2 min lightcurve for a single Sector)
        # timer3b = timer.time()
        # print('t3b', timer3b - timer0)   
        
        #::: global sigma clipping on the flattened flux (super fast)
        flux = sigma_clip(flux, sigma_upper=3, sigma_lower=20)
        # timer3c = timer.time()
        # print('t3c', timer3c - timer0)   
        
        if ('1' in options['show_plot']) or ('1' in options['save_plot']):
            fig, axes = plt.subplots(2,1, figsize=(40,8))
            axes[0].plot(time, flux_input, 'b.', rasterized=True)
            axes[0].plot(time, trend, 'r-', lw=2)
            axes[0].set(ylabel='Flux (input)', xticklabels=[])
            axes[1].plot(time, flux, 'b.', rasterized=True)
            axes[1].set(ylabel='Flux (detrended)', xlabel='Time (BJD)')
        if ('1' in options['save_plot']):
            try: fig.savefig(os.path.join(options['outdir'],'flux_'+wotan_kwargs['flatten']['method']+'.pdf'), bbox_inches='tight') #some matplotlib versions crash when saving pdf...
            except: fig.savefig(os.path.join(options['outdir'],'flux_'+wotan_kwargs['flatten']['method']+'.jpg'), bbox_inches='tight') #some matplotlib versions need pillow for jpg (conda install pillow)...
            if ('1' in options['show_plot']):
                plt.show(fig)
            else:
                plt.close(fig)
                
        if options['save_csv']:
            if flux_err is None: flux_err0 = np.nan*time
            else: flux_err0 = flux_err
            X = np.column_stack((time, flux, flux_err0, trend))
            np.savetxt(os.path.join(options['outdir'],'flux_'+wotan_kwargs['flatten']['method']+'.csv'), X, delimiter=',', header='time,flux_detrended,flux_err,trend')
        
        time_detrended = 1.*time #just for plotting
        flux_detrended = 1.*flux #just for plotting
        
    # timer3d = timer.time()
    # print('t3d', timer3d - timer0)    
    
    
    #::: search for transits
    i = 0
    ind_trs = []
    while (SNR >= tls_kwargs['SNR_threshold']) and (SDE >= tls_kwargs['SDE_threshold']) and (FAP <= tls_kwargs['FAP_threshold']) and (FOUND_SIGNAL==False):
        
        if options['quiet']:
            with open(os.devnull, 'w') as devnull:
                with contextlib.redirect_stdout(devnull):
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        model = tls(time, flux, flux_err)
                        results = model.power(**tls_kwargs_original)
        else:
            model = tls(time, flux, flux_err)
            results = model.power(**tls_kwargs_original)
            
        # timer4 = timer.time()
        # print('t4', timer4 - timer0)  
        
        # plt.figure()
        # plt.plot(time, flux, 'b.')
        # pprint(tls_kwargs_original)
        # pprint(results)
        # err
        
        if (results.snr >= tls_kwargs['SNR_threshold']) and (results.SDE >= tls_kwargs['SDE_threshold']) and (results.FAP <= tls_kwargs['FAP_threshold']):
            
            #::: calculcate the correct_duration, as TLS sometimes returns unreasonable durations
            ind_tr_phase = np.where( results['model_folded_model'] < 1. )[0]
            correct_duration = results['period'] * (results['model_folded_phase'][ind_tr_phase[-1]] - results['model_folded_phase'][ind_tr_phase[0]])
            
            #::: mark transit
            ind_tr, ind_out = index_transits(time_input, results['T0'], results['period'], correct_duration)
            ind_trs.append(ind_tr)
            
            #::: mask out detected transits and append results
            time1, flux1 = time, flux #for plotting
            time, flux, flux_err = mask(time, flux, flux_err, results.period, np.max((1.5*correct_duration)), results.T0)
            results_all.append(results)
            
            #::: write TLS stats to file
            with open(os.path.join(options['outdir'],'tls_signal_'+str(i)+'.txt'), 'wt') as out:
                pprint(results, stream=out)
    
            # timer5 = timer.time()
            # print('t5', timer5 - timer0)  
    
            #::: individual TLS plots
            if ('2' in options['show_plot']) or ('2' in options['save_plot']):
                fig = plt.figure(figsize=(20,8), tight_layout=True)
                gs = fig.add_gridspec(2,3)
                
                ax = fig.add_subplot(gs[0,:])
                ax.plot(time1, flux1, 'b.', rasterized=True)
                ax.plot(results['model_lightcurve_time'], results['model_lightcurve_model'], 'r-', lw=3)
                ax.set(xlabel='Time (BJD)', ylabel='Flux')
                
                ax = fig.add_subplot(gs[1,0])
                ax.plot(results['folded_phase'], results['folded_y'], 'b.', rasterized=True)
                ax.plot(results['model_folded_phase'], results['model_folded_model'], 'r-', lw=3)
                ax.set(xlabel='Phase', ylabel='Flux')
                
                ax = fig.add_subplot(gs[1,1])
                ax.plot((results['folded_phase']-0.5)*results['period']*24, results['folded_y'], 'b.', rasterized=True)
                ax.plot((results['model_folded_phase']-0.5)*results['period']*24, results['model_folded_model'], 'r-', lw=3)
                ax.set(xlim=[ -1.5*correct_duration*24, +1.5*correct_duration*24 ], xlabel='Time (h)', yticks=[])
                
                ax = fig.add_subplot(gs[1,2])
                ax.text( .02, 0.95, 'P = ' + np.format_float_positional(results['period'],4) + ' d', ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.85, 'Depth = ' + np.format_float_positional(1e3*(1.-results['depth']),4) + ' ppt', ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.75, 'Duration = ' + np.format_float_positional(24*correct_duration,4) + ' h', ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.65, 'T_0 = ' + np.format_float_positional(results['T0'],4) + ' d', ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.55, 'SNR = ' + np.format_float_positional(results['snr'],4), ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.45, 'SDE = ' + np.format_float_positional(results['SDE'],4), ha='left', va='center', transform=ax.transAxes )
                ax.text( .02, 0.35, 'FAP = ' + np.format_float_scientific(results['FAP'],4), ha='left', va='center', transform=ax.transAxes )
                ax.set_axis_off()
                if ('2' in options['save_plot']):
                    try: fig.savefig(os.path.join(options['outdir'],'tls_signal_'+str(i)+'.pdf'), bbox_inches='tight') #some matplotlib versions crash when saving pdf...
                    except: fig.savefig(os.path.join(options['outdir'],'tls_signal_'+str(i)+'.jpg'), bbox_inches='tight') #some matplotlib versions need pillow for jpg (conda install pillow)...
                if ('2' in options['show_plot']):
                    plt.show(fig)
                else:
                    plt.close(fig)
                    
            # timer6 = timer.time()
            # print('t6', timer6 - timer0)  
            
        SNR = results.snr
        SDE = results.SDE
        FAP = results.FAP
        if is_multiple_of(results['period'],options['inj_period']): SNR = -np.inf #if run as part of an inejction-recovery test, then abort if it matches the injected period
        i+=1
        
        
        
    #::: full lightcurve plot
    if ('3' in options['show_plot']) or ('3' in options['save_plot']):
        
        if detrend:
            fig, axes = plt.subplots(2,1, figsize=(40,8), tight_layout=True)
            ax = axes[0]
            ax.plot(time_input, flux_input, 'k.', color='grey', rasterized=True)
            ax.plot(time_input, trend, 'r-', lw=2)
            for number, ind_tr in enumerate(ind_trs):
                ax.plot(time_input[ind_tr], flux_input[ind_tr], marker='.', linestyle='none', label='signal '+str(number))
            ax.set(ylabel='Flux (input)', xticklabels=[])
            ax.legend()

            ax = axes[1]
            ax.plot(time_detrended, flux_detrended, 'k.', color='grey', rasterized=True)
            for number, ind_tr in enumerate(ind_trs):
                ax.plot(time_detrended[ind_tr], flux_detrended[ind_tr], marker='.', linestyle='none', label='signal '+str(number))
            ax.set(ylabel='Flux (detrended)', xlabel='Time (BJD)')
            ax.legend()
            
        else:
            fig = plt.figure(figsize=(20,4), tight_layout=True)
            fig, ax = plt.subplots(1,1, figsize=(40,4))
            ax.plot(time_input, flux_input, 'k.', color='grey', rasterized=True)
            ax.set(ylabel='Flux (input)', xlabel='Time (BJD)')
            for number, ind_tr in enumerate(ind_trs):
                ax.plot(time_input[ind_tr], flux_input[ind_tr], marker='.', linestyle='none', label='signal '+str(number))
            ax.legend()
        
        if ('3' in options['save_plot']):
            try: fig.savefig(os.path.join(options['outdir'],'tls_signal_all.pdf'), bbox_inches='tight') #some matplotlib versions crash when saving pdf...
            except: fig.savefig(os.path.join(options['outdir'],'tls_signal_all.jpg'), bbox_inches='tight') #some matplotlib versions need pillow for jpg (conda install pillow)...
        if ('3' in options['show_plot']):
            plt.show(fig)
        else:
            plt.close(fig)                    
                
            
    return results_all



###############################################################################
#::: TLS search using tessio
###############################################################################
def tls_search_by_tic(tic_id,
                      tls_kwargs=None, SNR_threshold=5., known_transits=None,
                      options=None):
    '''
    Summary:
    -------
    wrapper around tls_search()
    retrieves the SPOC PDC-SAP lightcurve
    retrieves all TIC catalog information from MAST
    calls tls_search()
    
    Inputs:
    -------
    tic_id : str
        TIC ID
        
    Optional Inputs:
    ----------------
    see tls_search()
        
    Returns:
    -------
    list of all TLS results
    '''
    
    #::: handle inputs
    if options is None: options = {}
    if 'show_plot' not in options: options['show_plot']=False
    if 'save_plot' not in options: options['save_plot']=False
    if 'outdir' not in options: options['outdir']=''
    
    #::: format inputs
    tic_id = str(int(tic_id))
    
    #::: load data and inject transit
    time, flux, flux_err = tessio.get(tic_id, pipeline='spoc', PDC=True, unpack=True)
    
    #::: load TIC info / tls kwargs
    tls_kwargs = get_tls_kwargs_by_tic(tic_id, tls_kwargs=tls_kwargs)
    
    return tls_search(time, flux, flux_err,
                      tls_kwargs=tls_kwargs,
                      SNR_threshold=SNR_threshold,
                      known_transits=known_transits,
                      options=options)



###############################################################################
#::: main
###############################################################################
if __name__ == '__main__':
    pass
    
    ###########################################################################
    #::: Example: search for a transit with TLS and tessio
    ###########################################################################
    # tic_id = '269701147'
    # SNR_threshold=5.,
    # known_transits = {'epoch':[2458715.3547, 2458726.0526, 2458743.5534],
    #                   'period':[8.8806, 28.5810, 38.3497],
    #                   'duration':[3.09/24., 4.45/24., 5.52/24.]
    #                  }
    
    # results_all = tls_search_by_tic(tic_id,
    #                 SNR_threshold=SNR_threshold,
    #                 known_transits=known_transits)
    # print(results_all)