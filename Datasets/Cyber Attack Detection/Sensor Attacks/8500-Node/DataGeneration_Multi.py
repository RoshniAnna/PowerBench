"""
In this file the false data injection attacks on bus voltage sensors is simulated
"""

import os
import pandas as pd
from GraphBuild import * 
import matplotlib.pyplot as plt
import random
import pickle
import uuid
import datetime
import gc
import gzip

# === SLURM array support ===
job_index = int(os.environ.get('SLURM_ARRAY_TASK_ID', 0))
total_jobs = 4  # Match with --array=0-3 in SLURM

NSc_normal = 2000  # total normal scenarios
NSc_attack = 2000  # total attack scenarios

sc_per_job = (NSc_normal + NSc_attack) // total_jobs
sc_normal_per_job = NSc_normal // total_jobs // 2  # each split to 2 types
sc_attack_per_job = NSc_attack // total_jobs // 2  # each split to 2 types

run_id = uuid.uuid4().hex[:8]
timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


### Building circuit

# Initialize Circuit
FolderName = os.path.dirname(os.path.realpath("__file__"))
DSSfile = os.path.join(FolderName, "Master.dss")
output_dir = os.path.join(FolderName, "results")
os.makedirs(output_dir, exist_ok=True)

Ckt_obj = CircuitSetup(DSSfile)  #creating a DSS object instance
#-- Equivalent Graph
G_original =  build_graph(Ckt_obj)
nx.readwrite.gml.write_gml(G_original,"8500NodeEx.gml") #Graph undirected with edge features and node features which are constant
node_list=list(G_original.nodes())
edge_list=list(G_original.edges())

### Loadshapes Restructure
input_file = 'LoadShape1.xlsx'
sheet_name = 'LoadShape1'  # Change to the sheet you want to split
df = pd.read_excel(input_file, sheet_name=sheet_name)

points_per_day = 24  # Assuming 1-hour resolution (24 points = 24 hours)
# Convert to flat list
loadshape_values = df.iloc[:, 0].values.tolist()
# Check total number of complete days available
num_days = len(loadshape_values) // points_per_day
# Slice the loadshape into daily
LoadShapes = []
for i in range(num_days):
    start_idx = i * points_per_day
    end_idx = (i + 1) * points_per_day
    daily_shape = loadshape_values[start_idx:end_idx]
    LoadShapes.append(daily_shape)
    
Ckt_obj.dss.Text.Command(f"New LoadShape.LoadVar")

#------------------------------------------------------------------------------------
### Define function to simulate different loading and extract time series information

def Powerflow_Timeseries(Ckt_obj, loadshape_day):
    # Edit the LoadShape in opendss
    Ckt_obj.dss.Text.Command(f"Edit LoadShape.LoadVar npts={len(loadshape_day)} interval=1 mult=(" + ' '.join(map(str, loadshape_day)) + ")")
    # Assign Load shapes
    Ckt_obj.dss.Text.Command("BatchEdit Load..* daily=LoadVar")
    
    # Time-series simulation        
    V_node_Sc = {bus: [] for bus in node_list}
    flow_branch_Sc= {br: [] for br in edge_list}
    
    Ckt_obj.dss.Text.Command("Set mode=daily")
    Ckt_obj.dss.Text.Command("Set stepsize=30m")
    Ckt_obj.dss.Text.Command("Set number=1")
    t= 0
    while t<24:
            Ckt_obj.dss.Solution.Solve()
            # Get Node Voltages
            for bus in node_list:
                V=Bus(Ckt_obj,bus).Vmag
                V_node_Sc[bus].append(V) 
                
            # Get branchflows  
            for (u,v) in edge_list:
                branch_label = G_original[u][v]['Label']
                branch_device = G_original[u][v]['Device']
                branch_elem = f"{branch_device}.{branch_label}"
                branch_pflow = Branch(Ckt_obj,branch_elem).flow
                branchflow = np.sum(branch_pflow)
                flow_branch_Sc[(u,v)].append(branchflow)
        
            t = Ckt_obj.dss.Solution.DblHour()
        
    return  V_node_Sc, flow_branch_Sc
    

### Define function to inject attacks in the time series voltage
def inject_voltage_attack(BusVoltages, start_idx, end_idx, attack_mult):
    for i in range(start_idx, end_idx):
        BusVoltages[i] = np.array([v * attack_mult for v in BusVoltages[i]]) #array if necessary
    return BusVoltages
#------------------------------------------------------------------------------------

Scenarios  = []
scid = 0
batch_size = 500
batch_count = 0

def write_batch():
    global batch_count, Scenarios
    if Scenarios:
        part_file = os.path.join(output_dir, f"{timestamp}_{run_id}_SensorAttacks_8500_job{job_index}_part{batch_count}.pkl.gz")
        with gzip.open(part_file, 'wb') as f:
            pickle.dump(Scenarios, f)
        print(f"--> Wrote batch {batch_count} with {len(Scenarios)} scenarios to {part_file}", flush=True)
        batch_count += 1

        # Clear the batch from memory and force garbage collection
        Scenarios.clear()
        gc.collect()

# Normal case- normal operation
for _ in range(sc_normal_per_job):
    print(f"Normal case: Scenario {scid}", flush =True)
    # Varying Load shapes
    loadshape_day = random.choice(LoadShapes)
    V_node_Sc, flow_branch_Sc = Powerflow_Timeseries(Ckt_obj, loadshape_day)
    Scenarios.append({'Index':scid, 'Anomalous':'No', 'Targeted Buses': [], 'Attack Type': 'Nil', 'BusVoltage series':V_node_Sc,'BranchFlow series':flow_branch_Sc})
    scid = scid + 1
    if len(Scenarios) >= batch_size:
        write_batch()
    
# Normal case - over or underloading due to unforseen events
for _ in range(sc_normal_per_job):
    print(f"Normal case: Scenario {scid}", flush =True)
    # Varying Load shapes
    loadshape_day = random.choice(LoadShapes)
    over_under_flag = random.choice([0,1])
    if over_under_flag == 0:
        loadvar_factor = random.uniform(1.1, 1.5) # Overload
    elif over_under_flag == 1:
        loadvar_factor = random.uniform(0.3, 0.9) # Underload
    
    total_steps = 24
    min_step = 1
    max_step = 6
    start_idx = random.randint(0, total_steps - min_step)
    max_length = total_steps - start_idx  
    loadvar_length = random.randint(min_step, min(max_step, max_length)) # Ensure don't exceed total steps
    # Compute end index
    end_idx = start_idx + loadvar_length
    
    variedloadedshape = loadshape_day.copy()
    for i in range(start_idx, end_idx):
        variedloadedshape[i] = variedloadedshape[i] * loadvar_factor
    V_node_Sc, flow_branch_Sc = Powerflow_Timeseries(Ckt_obj, variedloadedshape)
    Scenarios.append({'Index':scid, 'Anomalous':'No', 'Targeted Buses': [], 'Attack Type': 'Nil', 'BusVoltage series':V_node_Sc,'BranchFlow series':flow_branch_Sc})
    scid = scid + 1
    if len(Scenarios) >= batch_size:
        write_batch()    

# Undervoltage attack at sensor(s)
for _ in range(sc_attack_per_job):
    print(f"Undervoltage case: Scenario {scid}", flush =True)
    # Varying Load shapes
    loadshape_day = random.choice(LoadShapes)
    V_node_Sc, flow_branch_Sc = Powerflow_Timeseries(Ckt_obj, loadshape_day)
    # Vary factor - under
    attack_mult = random.uniform(0,0.85) # Under Voltage    
    # Vary attack window
    total_steps = 48 #24 hours with 30 minutes
    # Min attack window is 2 steps 1 hour and max is 12 steps - 6 hours
    # Choose a random start index
    start_idx = random.randint(0, total_steps - 2)
    max_attack_length = total_steps - start_idx  
    attack_length = random.randint(2, min(12, max_attack_length)) # Ensure don't exceed total steps
    # Compute end index
    end_idx = start_idx + attack_length
    # Single or Multi attacks
    num_nodes = random.randint(1, 5)  #15%
    attack_buses = random.sample(node_list, k=num_nodes)     
    for atk_bus in attack_buses:
        attacked_voltages = inject_voltage_attack(V_node_Sc[atk_bus], start_idx, end_idx, attack_mult) 
        V_node_Sc[atk_bus] = attacked_voltages
    Scenarios.append({'Index':scid, 'Anomalous':'Yes', 'Targeted Buses': attack_buses, 'Attack Type': 'Under Voltage', 'BusVoltage series':V_node_Sc,'BranchFlow series':flow_branch_Sc})
    scid = scid + 1
    if len(Scenarios) >= batch_size:
        write_batch()
                
# Overvoltage attack on sensor(s)
for _ in range(sc_attack_per_job):
    print(f"Overvoltage case: Scenario {scid}", flush =True)
    # Varying Load shapes
    loadshape_day = random.choice(LoadShapes)
    V_node_Sc, flow_branch_Sc = Powerflow_Timeseries(Ckt_obj, loadshape_day)
    # Vary factor - over
    attack_mult = random.uniform(1.05,1.3) # Over Voltage   
    # Vary attack window
    total_steps = 48 #24 hours with 30 minutes
    # Min attack window is 2 steps 1 hour and max is 12 steps - 6 hours
    # Choose a random start index
    start_idx = random.randint(0, total_steps - 2)
    max_attack_length = total_steps - start_idx
    attack_length = random.randint(2, min(12, max_attack_length)) # Ensure don't exceed total steps
    # Compute end index
    end_idx = start_idx + attack_length
    # Single or Multi attacks
    num_nodes = random.randint(1, 5)  #15%
    attack_buses = random.sample(node_list, k=num_nodes)     
    for atk_bus in attack_buses:
        attacked_voltages = inject_voltage_attack(V_node_Sc[atk_bus], start_idx, end_idx, attack_mult) 
        V_node_Sc[atk_bus] = attacked_voltages
    Scenarios.append({'Index':scid, 'Anomalous':'Yes', 'Targeted Buses': attack_buses, 'Attack Type': 'Over Voltage', 'BusVoltage series':V_node_Sc,'BranchFlow series':flow_branch_Sc})
    scid = scid + 1
    if len(Scenarios) >= batch_size:
        write_batch()
            
# Write any remaining scenarios
write_batch()

